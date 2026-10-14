from __future__ import annotations

import base64
import gc
import logging
from functools import wraps
import io
import math
import hashlib
import json
import os
import random
import re
import shutil
import time
import html
import zipfile
from bisect import bisect_right
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from itertools import product
from pathlib import Path
from threading import Event, RLock, Thread, local
from typing import Any, Callable, Literal
from urllib.parse import urlparse
from uuid import uuid4
import xml.etree.ElementTree as ET

import httpx
import numpy as np
from pydantic import ValidationError

from .utils.persistence import StatePersistenceError, StateLoadError, read_state, write_state


from .models import (
    AIAnalysisRecord,
    AIProviderTestResponse,
    AISourceConfig,
    AIProviderConfig,
    AppConfig,
    CandlePoint,
    BacktestRunRequest,
    BacktestPlateauRunRequest,
    BacktestPlateauResponse,
    BacktestPlateauPoint,
    BacktestPlateauParams,
    BacktestPlateauRegionSummary,
    BacktestPlateauCorrelationRow,
    BacktestPlateauTaskProgress,
    BacktestPlateauTaskStatusResponse,
    BacktestPlateauTaskListResponse,
    BacktestPlateauTaskDeleteResponse,
    BacktestPlateauPointDetailResponse,
    CrossValidateRequest,
    CrossValidateResponse,
    CrossValidateTaskProgress,
    CrossValidateTaskStatusResponse,
    CrossValidateStockResult,
    CrossValidateStockDetail,
    CrossValidateHistoryRecord,
    CrossValidateHistoryDetail,
    CrossValidateBacktestRequest,
    CrossValidateBacktestResponse,
    CrossValidateBacktestStockResult,
    BacktestPoolRollMode,
    BacktestResponse,
    BacktestTrade,
    BacktestRiskMetrics,
    BacktestStabilityDiagnostics,
    BacktestRegimeBucket,
    BacktestMonteCarloSummary,
    BacktestWalkForwardFold,
    BacktestWalkForwardReport,
    BacktestTaskProgress,
    BacktestTaskStageTiming,
    BacktestTaskListResponse,
    BacktestTaskDeleteResponse,
    BacktestTaskStatusResponse,
    BacktestReportBuildRequest,
    BacktestReportBuildResponse,
    BacktestReportDetail,
    BacktestReportImportResponse,
    BacktestReportListResponse,
    BacktestReportManifest,
    BacktestReportManifestApp,
    BacktestStrategySignalPoint,
    BacktestStrategySignalStrategyInfo,
    BacktestStrategySignalsResponse,
    BacktestReportManifestFile,
    BacktestReportSummary,
    BoardFilter,
    Market,
    CreateOrderRequest,
    CreateOrderResponse,
    IntradayPayload,
    IntradayPoint,
    MarketDataSyncRequest,
    MarketDataSyncResponse,
    MarketNewsItem,
    MarketNewsResponse,
    PortfolioPosition,
    PortfolioSnapshot,
    ReviewResponse,
    ReviewStats,
    ReviewTag,
    ReviewTagCreateRequest,
    ReviewTagStatItem,
    ReviewTagStatsResponse,
    ReviewTagsPayload,
    ReviewTagType,
    ScreenerMode,
    ScreenerParams,
    ScreenerResult,
    ScreenerRunDetail,
    ScreenerStep1Config,
    ScreenerStep2Config,
    ScreenerStep3Config,
    ScreenerStep4Config,
    ScreenerStepConfigs,
    ScreenerStepPools,
    ScreenerStepSummary,
    SignalScanMode,
    SignalEtfBacktestConstituentDetail,
    SignalEtfBacktestConstituentInput,
    SignalEtfBacktestAutoCreateIssue,
    SignalEtfBacktestAutoCreateItem,
    SignalEtfBacktestAutoCreateRequest,
    SignalEtfBacktestAutoCreateResponse,
    SignalEtfBacktestCreateRequest,
    SignalEtfBacktestCurvePoint,
    SignalEtfBacktestDetail,
    SignalEtfBacktestListResponse,
    SignalEtfBacktestPerformance,
    SignalEtfBacktestRecord,
    SignalEtfBacktestStrategyStats,
    SignalEtfBacktestSummary,
    SignalEtfBacktestUpdateRequest,
    SignalResult,
    SignalsResponse,
    StrategyCatalogResponse,
    StrategyCapabilities,
    StrategyDescriptor,
    EventJudgmentCatalogResponse,
    EventJudgmentDimension,
    EventJudgmentMetricOption,
    EventJudgmentProfile,
    EventJudgmentRuleOption,
    EventJudgmentRuleValue,
    EventJudgmentProfileDeleteResponse,
    EventJudgmentProfileUpsertRequest,
    TrendPoolStep,
    SystemStorageStatus,
    SimFillsResponse,
    SimOrdersResponse,
    SimResetResponse,
    SimSettleResponse,
    SimTradeFill,
    SimTradeOrder,
    SimTradingConfig,
    Stage,
    DailyReviewListResponse,
    DailyReviewPayload,
    DailyReviewRecord,
    StockAnalysis,
    StockAnalysisResponse,
    StockAnnotation,
    TradeFillTagAssignment,
    TradeFillTagUpdateRequest,
    ThemeStage,
    TradeRecord,
    TrendClass,
    WeeklyReviewListResponse,
    WeeklyReviewPayload,
    WeeklyReviewRecord,
    WyckoffEventStoreBackfillRequest,
    WyckoffEventStoreBackfillResponse,
    WyckoffEventStoreStatsResponse,
    SentimentValuationQuoteResponse,
)
from .market_data_sync import sync_baostock_daily
from .sim_engine import SimAccountEngine
from .tdx_loader import (
    load_candles_for_symbol,
    load_input_pool_from_tdx,
    load_intraday_for_symbol_date,
    probe_tdx_latest_trade_date,
)

# Import refactored modules
from .utils.text_utils import TextProcessor, URLUtils
from .utils.keep_awake import keep_awake_while
from .core.signal_analyzer import SignalAnalyzer, WYCKOFF_ACC_EVENTS, WYCKOFF_RISK_EVENTS, WYCKOFF_EVENT_ORDER
from .core.ai_analyzer import AIAnalyzer, create_ai_analyzer
from .core.ai_playbook import build_playbook, build_playbook_text
from .core.ai_local_store import load_local_playbook, save_local_playbook, load_quick_prompts, save_quick_prompts
from .core.ai_context_builder import AIContextBuilder
from .core.ai_chat_service import AIChatService, normalize_ai_conclusion
from .core.ai_parameter_proposal import AIParameterProposalService
from .core.backtest_engine import BacktestEngine, CandidateTrade
from .core.backtest_matrix_engine import BacktestMatrixEngine, MatrixBundle
from .core.backtest_signal_matrix import BacktestSignalMatrix, compute_backtest_signal_matrix
from .core.emotion_limit_up_strategy import (
    calculate_emotion_limit_up_signal,
    evaluate_emotion_limit_up_signal,
)
from .core.limit_up_arb_strategy import (
    calculate_limit_up_arb_signal,
    evaluate_limit_up_arb_signal,
)
from .core.force_rhythm_strategy import (
    calculate_force_rhythm_signal,
    evaluate_force_rhythm_exit,
    evaluate_force_rhythm_signal,
)
from .core.ths_volume_signal import calculate_ths_main_retail_signal
from .core.strategy_plugins import calculate_wulong_cluster_signal, evaluate_wulong_cluster_signal
from .core.b1_strategy import calculate_b1_signal, evaluate_b1_signal
from .core.trend_king_strategy import calculate_trend_king_signal, evaluate_trend_king_signal
from .core.sector_analyzer import SectorAnalysisCache
from .core.strategy_registry import StrategyRegistry
from .core.wyckoff_event_store import WyckoffEventStore, build_wyckoff_params_hash
from .core.screener import ScreenerEngine, create_screener_engine, THEME_STAGES
from .core.candle_analyzer import CandleAnalyzer, create_candle_analyzer
from .core.sentiment_valuation import (
    calc_implied_sentiment,
    count_limit_ups,
    index_points_to_coef,
    parse_eastmoney_quote,
    suggest_base_pe,
    suggest_sentiment_from_activity,
    symbol_to_secid,
)
from .providers.web_provider import RSSWebEvidenceProvider, SearchWebEvidenceProvider
from .config import ConfigManager, create_config_manager, ConfigValidator
from .state_manager import StateManager, create_state_manager

STOCK_POOL: list[dict[str, str]] = [
    {"symbol": "sh600519", "name": "贵州茅台", "trend": "A", "stage": "Mid"},
    {"symbol": "sz300750", "name": "宁德时代", "trend": "A_B", "stage": "Early"},
    {"symbol": "sh601899", "name": "紫金矿业", "trend": "A", "stage": "Mid"},
    {"symbol": "sz002594", "name": "比亚迪", "trend": "A_B", "stage": "Mid"},
    {"symbol": "sh600030", "name": "中信证券", "trend": "A", "stage": "Early"},
    {"symbol": "sz000333", "name": "美的集团", "trend": "A", "stage": "Late"},
    {"symbol": "sh688041", "name": "海光信息", "trend": "B", "stage": "Late"},
    {"symbol": "sz002230", "name": "科大讯飞", "trend": "A_B", "stage": "Mid"},
]



class BacktestValidationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = str(code or "BACKTEST_INVALID")


class BacktestTaskCancelledError(RuntimeError):
    pass


class CrossValidateCancelledError(RuntimeError):
    pass


def state_locked(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return wrapped


class MarketDataUnavailable(ValueError):
    pass


class InMemoryStore:
    _APP_STATE_SCHEMA_VERSION = 3
    _FULL_MARKET_SYSTEM_PROTECT_LIMIT = 6000
    _BACKTEST_INPUT_POOL_CACHE_VERSION = "input-pool-v3"
    _SCREENER_RESULT_CACHE_VERSION = "screener-run-v3"
    _SIGNALS_RESULT_CACHE_VERSION = "signals-v2"
    _BACKTEST_TREND_FILTER_CACHE_VERSION = "trend-filter-v4"
    _BACKTEST_RESULT_CACHE_VERSION = "backtest-result-v7-strategy-registration"
    _BACKTEST_SIGNAL_MATRIX_CACHE_VERSION = "signal-matrix-v2"
    _BACKTEST_PRECHECK_CACHE_VERSION = "precheck-v1"
    _BACKTEST_REPORT_SCHEMA_VERSION = "ftbt-1.0"
    _BACKTEST_REPORT_PACKAGE_TYPE = "backtest_report"
    _BACKTEST_REPORT_REQUIRED_FILES = (
        "run_request.json",
        "run_result.json",
        "report.xlsx",
        "report.html",
    )
    _BACKTEST_IMPORTED_TASK_ID_MAX_LENGTH = 64
    _IMPORTED_BACKTEST_TASK_PREFIX = "imp_"
    _IMPORTED_PLATEAU_TASK_PREFIX = "imp_plateau_"
    _BACKTEST_REPORT_PLATEAU_POINT_DETAIL_FILE_PREFIX = "plateau_point_detail__"
    _BACKTEST_REPORT_PLATEAU_POINT_DETAIL_FILE_SUFFIX = ".json"
    _BACKTEST_REPORT_META_SCHEMA_VERSION = 1
    _BACKTEST_REPORT_ID_RE = re.compile(r"^[A-Za-z0-9._-]{4,96}$")
    _BACKTEST_TASK_ID_RE = re.compile(r"^[A-Za-z0-9._-]{4,96}$")
    _BACKTEST_PLATEAU_POINT_DETAIL_KEY_RE = re.compile(r"^[A-Za-z0-9._-]{4,96}$")
    _EVENT_GRADE_RANK = {"C": 1, "B": 2, "A": 3}
    _EVENT_JUDGMENT_DEFAULT_PROFILE_ID = "system_legacy_formula_v1"
    _BACKTEST_MATRIX_TIMING_RE = re.compile(
        r"耗时\[建矩阵=(?P<matrix>[\d.]+)s,\s*算信号=(?P<signal>[\d.]+)s,\s*撮合=(?P<match>[\d.]+)s,\s*总计=(?P<total>[\d.]+)s\]"
    )
    _BACKTEST_EXEC_DETAIL_TIMING_RE = re.compile(
        r"执行细分耗时\[候选=(?P<candidate>[\d.]+)s,\s*撮合=(?P<match>[\d.]+)s,\s*曲线=(?P<curve>[\d.]+)s\]"
    )

    def __init__(self, app_state_path: str | None = None, sim_state_path: str | None = None) -> None:
        self._lock = RLock()
        self._candles_lock = RLock()
        self._candles_map: dict[str, list[CandlePoint]] = {}
        self._candles_signatures: dict[str, tuple] = {}
        self._state_warnings: list[str] = []
        self._run_store: dict[str, ScreenerRunDetail] = {}
        self._annotation_store: dict[str, StockAnnotation] = {}
        self._config: AppConfig = self._default_config()
        self._latest_rows: dict[str, ScreenerResult] = {}
        self._ai_record_store: list[AIAnalysisRecord] = self._default_ai_records()
        self._daily_review_store: dict[str, DailyReviewRecord] = {}
        self._weekly_review_store: dict[str, WeeklyReviewRecord] = {}
        self._signal_etf_backtest_store: dict[str, dict[str, Any]] = {}
        self._review_tags: dict[ReviewTagType, list[ReviewTag]] = self._default_review_tags()
        self._fill_tag_store: dict[str, TradeFillTagAssignment] = {}
        self._web_evidence_cache: dict[str, tuple[float, list[dict[str, str]]]] = {}
        self._market_news_last_success: tuple[float, list[dict[str, str]], dict[str, str]] | None = None
        self._quote_profile_cache: dict[str, tuple[float, dict[str, str]]] = {}
        self._signals_cache: dict[str, tuple[float, SignalsResponse]] = {}
        self._backtest_tasks: dict[str, BacktestTaskStatusResponse] = {}
        self._backtest_task_payloads: dict[str, BacktestRunRequest] = {}
        self._backtest_task_lock = RLock()
        self._backtest_running_worker_ids: set[str] = set()
        self._backtest_runtime_context = local()
        self._backtest_task_state_path = self._resolve_backtest_task_state_path()
        self._backtest_task_state_last_persist_at = 0.0
        self._backtest_plateau_tasks: dict[str, BacktestPlateauTaskStatusResponse] = {}
        self._backtest_plateau_task_payloads: dict[str, BacktestPlateauRunRequest] = {}
        self._backtest_plateau_task_lock = RLock()
        self._backtest_plateau_running_worker_ids: set[str] = set()
        self._backtest_plateau_task_state_path = self._resolve_backtest_plateau_task_state_path()
        self._backtest_plateau_task_state_last_persist_at = 0.0
        self._cross_validate_tasks: dict[str, CrossValidateTaskStatusResponse] = {}
        self._cross_validate_task_payloads: dict[str, CrossValidateRequest] = {}
        self._cross_validate_cancel_events: dict[str, Event] = {}
        self._cross_validate_task_lock = RLock()
        self._cross_validate_running_worker_ids: set[str] = set()
        self._backtest_matrix_engine = BacktestMatrixEngine()
        self._strategy_registry = StrategyRegistry()
        self._ai_context_builder = AIContextBuilder(
            resolve_symbol_name=lambda symbol, row: self._resolve_symbol_name(symbol, row),
            get_screener_row=lambda symbol: self._latest_rows.get(symbol),
            build_row_from_candles=lambda symbol: self._build_row_from_candles(symbol),
            build_ai_context_text=lambda symbol, row: self._build_ai_context_text(symbol, row),
            compose_prompt_context=lambda symbol, row, urls: self._compose_ai_prompt_context(symbol, row, urls),
            enabled_source_urls=lambda: self._enabled_ai_source_urls(),
            get_latest_ai_record=lambda symbol: self._get_latest_ai_record_dict(symbol),
            get_screener_run=lambda run_id: self.get_screener_run(run_id),
            get_backtest_report=lambda report_id: self.get_backtest_report(report_id),
            get_strategy_descriptor=lambda strategy_id: self._strategy_registry.get(strategy_id),
            get_sentiment_valuation_quote=lambda symbol: self.get_sentiment_valuation_quote(symbol),
        )
        self._ai_chat_service = AIChatService(
            self._strategy_registry,
            resolve_provider=lambda: self._active_ai_provider(),
            resolve_api_key=lambda provider: self._resolve_provider_api_key(provider),
            enrich_context=lambda ctx: self._ai_context_builder.enrich(ctx),
            load_user_principles=lambda: load_local_playbook().get("principles", []),
            timeout_sec=lambda: float(self._config.ai_timeout_sec),
            retry_count=lambda: int(self._config.ai_retry_count),
        )
        self._ai_parameter_proposal_service = AIParameterProposalService(
            self._strategy_registry,
            resolve_provider=lambda: self._active_ai_provider(),
            resolve_api_key=lambda provider: self._resolve_provider_api_key(provider),
            enrich_context=lambda ctx: self._ai_context_builder.enrich(ctx),
            load_user_principles=lambda: load_local_playbook().get("principles", []),
            timeout_sec=lambda: float(self._config.ai_timeout_sec),
            get_config=lambda: self.get_config(),
            set_config=lambda payload: self.set_config(payload),
        )
        self._backtest_matrix_algo_version = os.getenv("TDX_TREND_BACKTEST_MATRIX_ALGO_VERSION", "").strip() or "matrix-v1"
        self._backtest_signal_matrix_runtime_cache: dict[str, tuple[float, BacktestSignalMatrix]] = {}
        self._backtest_signal_matrix_runtime_cache_lock = RLock()
        self._backtest_input_pool_runtime_cache: dict[str, tuple[float, list[ScreenerResult], str | None]] = {}
        self._backtest_input_pool_runtime_cache_lock = RLock()
        self._backtest_precheck_cache: dict[str, tuple[float, str | None, str | None]] = {}
        self._backtest_precheck_cache_lock = RLock()
        self._wyckoff_event_store_enabled = self._env_flag("TDX_TREND_WYCKOFF_STORE_ENABLED", True)
        self._wyckoff_event_store_read_only = self._env_flag("TDX_TREND_WYCKOFF_STORE_READ_ONLY", False)
        self._wyckoff_event_algo_version = os.getenv("TDX_TREND_WYCKOFF_ALGO_VERSION", "").strip() or "wyckoff-v1"
        self._wyckoff_event_data_version = os.getenv("TDX_TREND_WYCKOFF_DATA_VERSION", "").strip() or "default"
        self._wyckoff_event_store = WyckoffEventStore(
            self._resolve_wyckoff_event_store_path(),
            enabled=self._wyckoff_event_store_enabled,
            read_only=self._wyckoff_event_store_read_only,
        )
        self._wyckoff_metrics_lock = RLock()
        self._sector_cache = SectorAnalysisCache()
        self._wyckoff_metrics: dict[str, object] = {
            "cache_hits": 0,
            "cache_misses": 0,
            "snapshot_reads": 0,
            "snapshot_read_ms_total": 0.0,
            "lazy_fill_writes": 0,
            "backfill_runs": 0,
            "backfill_writes": 0,
            "quality_empty_events": 0,
            "quality_score_outliers": 0,
            "quality_date_misaligned": 0,
            "last_backfill_started_at": None,
            "last_backfill_finished_at": None,
            "last_backfill_duration_sec": None,
            "last_backfill_scan_dates": 0,
            "last_backfill_symbols": 0,
            "last_backfill_quality_empty_events": 0,
            "last_backfill_quality_score_outliers": 0,
            "last_backfill_quality_date_misaligned": 0,
        }
        self._event_judgment_metric_options = self._build_event_judgment_metric_options()
        self._event_judgment_rule_options = self._build_event_judgment_rule_options()
        self._event_judgment_rule_option_map = {
            str(item.get("rule_key", "")).strip(): dict(item)
            for item in self._event_judgment_rule_options
            if str(item.get("rule_key", "")).strip()
        }
        self._event_judgment_system_profiles = self._build_event_judgment_system_profiles()
        self._event_judgment_custom_profiles: dict[str, dict[str, Any]] = {}
        self._active_event_judgment_profile_id = self._default_event_judgment_profile_id()
        self._app_state_path = self._resolve_app_state_path(app_state_path)
        self._load_or_init_app_state()
        self._sim_engine = SimAccountEngine(
            get_candles=self._ensure_candles,
            resolve_symbol_name=self._resolve_symbol_name,
            now_date=self._now_date,
            now_datetime=self._now_datetime,
            state_path=sim_state_path or os.getenv("TDX_TREND_SIM_STATE_PATH", "").strip() or None,
        )
        self._load_backtest_task_state()
        self._resume_backtest_tasks_after_boot()
        self._load_backtest_plateau_task_state()
        self._resume_backtest_plateau_tasks_after_boot()

    @staticmethod
    def _resolve_user_path(value: str) -> Path:
        expanded = os.path.expandvars(os.path.expanduser(str(value).strip()))
        return Path(expanded)

    @staticmethod
    def _env_flag(name: str, default: bool) -> bool:
        raw = os.getenv(name, "").strip().lower()
        if not raw:
            return default
        if raw in {"1", "true", "yes", "y", "on"}:
            return True
        if raw in {"0", "false", "no", "n", "off"}:
            return False
        return default

    @classmethod
    def _normalize_event_grade(cls, raw: Any) -> str:
        text = str(raw or "C").strip().upper()
        return text if text in cls._EVENT_GRADE_RANK else "C"

    @classmethod
    def _event_grade_meets_threshold(cls, *, grade: str, minimum: str) -> bool:
        normalized_grade = cls._normalize_event_grade(grade)
        normalized_minimum = cls._normalize_event_grade(minimum)
        return cls._EVENT_GRADE_RANK.get(normalized_grade, 1) >= cls._EVENT_GRADE_RANK.get(normalized_minimum, 1)

    @staticmethod
    def _normalize_confirmation_status(raw: Any) -> str:
        text = str(raw or "").strip().lower()
        return text if text in {"confirmed", "partial", "unconfirmed", "risk_blocked"} else "unconfirmed"

    def _resolve_strategy_runtime(
        self,
        *,
        strategy_id: str | None,
        strategy_params: dict[str, Any] | None,
    ) -> tuple[dict[str, Any], dict[str, Any], str]:
        normalized_id = self._strategy_registry.normalize_strategy_id(strategy_id)
        descriptor = self._strategy_registry.get(normalized_id)
        if descriptor is None:
            available = ",".join(item.strategy_id for item in self._strategy_registry.list())
            raise BacktestValidationError(
                "STRATEGY_NOT_FOUND",
                f"策略不存在: {normalized_id}（可用: {available}）",
            )
        if not descriptor.enabled:
            raise BacktestValidationError("STRATEGY_DISABLED", f"策略已禁用: {normalized_id}")
        normalized_default_params = self._strategy_registry.normalize_params(
            descriptor.strategy_id,
            dict(descriptor.default_params),
        )
        normalized_override_params = self._strategy_registry.normalize_params(
            descriptor.strategy_id,
            strategy_params if isinstance(strategy_params, dict) else {},
        )
        normalized_params = dict(normalized_default_params)
        normalized_params.update(normalized_override_params)
        params_hash = self._strategy_registry.params_hash(normalized_params)
        return (
            {
                "strategy_id": descriptor.strategy_id,
                "name": descriptor.name,
                "version": descriptor.version,
                "enabled": bool(descriptor.enabled),
                "is_default": bool(descriptor.is_default),
                "capabilities": {
                    "supports_matrix": bool(descriptor.capabilities.supports_matrix),
                    "supports_signal_age_filter": bool(descriptor.capabilities.supports_signal_age_filter),
                    "supports_entry_delay": bool(descriptor.capabilities.supports_entry_delay),
                },
            },
            normalized_params,
            params_hash,
        )

    def _apply_strategy_overrides_to_backtest_payload(
        self,
        payload: BacktestRunRequest,
        *,
        strategy_id: str,
        normalized_strategy_params: dict[str, Any],
    ) -> BacktestRunRequest:
        overrides = self._strategy_registry.resolve_backtest_overrides(strategy_id, normalized_strategy_params)
        updates: dict[str, Any] = {
            "strategy_id": strategy_id,
            "strategy_params": normalized_strategy_params,
        }
        for key, value in overrides.items():
            updates[key] = value
        return payload.model_copy(update=updates)

    @staticmethod
    def _build_strategy_snapshot_note(
        *,
        strategy_meta: dict[str, Any],
        strategy_params_hash: str,
        strategy_params: dict[str, Any],
    ) -> str:
        capabilities = strategy_meta.get("capabilities", {}) if isinstance(strategy_meta, dict) else {}
        matrix_flag = "1" if bool(capabilities.get("supports_matrix")) else "0"
        age_flag = "1" if bool(capabilities.get("supports_signal_age_filter")) else "0"
        delay_flag = "1" if bool(capabilities.get("supports_entry_delay")) else "0"
        return (
            f"策略快照: id={strategy_meta.get('strategy_id', 'unknown')}, "
            f"version={strategy_meta.get('version', 'unknown')}, "
            f"params={strategy_params_hash}, "
            f"cap=matrix:{matrix_flag}|age:{age_flag}|delay:{delay_flag}, "
            f"params_count={len(strategy_params)}"
        )

    def _apply_strategy_metadata_to_backtest_result(
        self,
        result: BacktestResponse,
        *,
        strategy_meta: dict[str, Any],
        strategy_params: dict[str, Any],
        strategy_params_hash: str,
        strategy_note: str,
        execution_path: Literal["matrix", "legacy"] | None = None,
        effective_payload: BacktestRunRequest | None = None,
    ) -> BacktestResponse:
        notes = list(result.notes)
        if strategy_note and strategy_note not in notes:
            notes.insert(0, strategy_note)
        path_text = str(execution_path or "").strip().lower()
        if path_text not in {"matrix", "legacy"}:
            result_path = str(result.execution_path or "").strip().lower()
            if result_path in {"matrix", "legacy"}:
                path_text = result_path
            elif any("矩阵引擎已启用" in str(item) for item in notes):
                path_text = "matrix"
            else:
                path_text = "legacy"
        updates: dict[str, Any] = {
            "strategy_id": str(strategy_meta.get("strategy_id") or "wyckoff_trend_v1"),
            "strategy_version": str(strategy_meta.get("version") or "1.0.0"),
            "strategy_params": dict(strategy_params),
            "strategy_params_hash": str(strategy_params_hash),
            "execution_path": path_text,
        }
        if effective_payload is not None:
            updates["effective_run_request"] = effective_payload.model_copy(deep=True)
        if notes != result.notes:
            updates["notes"] = notes
        return result.model_copy(update=updates)

    @classmethod
    def _default_event_judgment_profile_id(cls) -> str:
        return str(cls._EVENT_JUDGMENT_DEFAULT_PROFILE_ID)

    @staticmethod
    def _build_event_judgment_metric_options() -> list[dict[str, str]]:
        return [
            {
                "metric_key": "event_background_score",
                "label": "背景分",
                "description": "趋势背景与风险环境（高分更优）",
            },
            {
                "metric_key": "event_position_score",
                "label": "位置分",
                "description": "事件发生位置是否处于理想区间（高分更优）",
            },
            {
                "metric_key": "event_vol_price_score",
                "label": "量价分",
                "description": "量价协同与主导方向（高分更优）",
            },
            {
                "metric_key": "event_confirmation_score",
                "label": "确认分",
                "description": "序列与关键确认状态（高分更优）",
            },
            {
                "metric_key": "phase_context_score",
                "label": "阶段语境分",
                "description": "前置事件完整度与阶段一致性（高分更优）",
            },
            {
                "metric_key": "event_recency_score",
                "label": "新鲜度分",
                "description": "事件时间衰减后的有效性（高分更优）",
            },
            {
                "metric_key": "candle_quality_score",
                "label": "K线质量分",
                "description": "事件K线形态质量（高分更优）",
            },
            {
                "metric_key": "cost_center_shift_score",
                "label": "筹码迁移分",
                "description": "筹码重心迁移质量（高分更优）",
            },
            {
                "metric_key": "weekly_context_score",
                "label": "周线语境分",
                "description": "周线环境对日线事件的放大质量（高分更优）",
            },
            {
                "metric_key": "risk_score",
                "label": "风险分",
                "description": "风险越高分越高，通常建议勾选反向（invert）",
            },
        ]

    @staticmethod
    def _build_event_judgment_rule_options() -> list[dict[str, Any]]:
        specs: list[dict[str, Any]] = [
            # Core windows
            {"rule_key": "lookback_core_days", "label": "核心回看天数", "category": "基础窗口", "value_type": "integer", "default": 40, "min": 20, "max": 120, "step": 1, "description": "事件搜索起始窗口（天）"},
            {"rule_key": "sc_scan_lookback_days", "label": "SC搜索窗口", "category": "基础窗口", "value_type": "integer", "default": 35, "min": 15, "max": 120, "step": 1, "description": "近期SC锚点搜索窗口（天）"},
            {"rule_key": "bc_scan_lookback_days", "label": "BC搜索窗口", "category": "基础窗口", "value_type": "integer", "default": 35, "min": 15, "max": 120, "step": 1, "description": "近期BC锚点搜索窗口（天）"},
            {"rule_key": "pattern_window_days", "label": "中后期模式窗口", "category": "基础窗口", "value_type": "integer", "default": 28, "min": 10, "max": 80, "step": 1, "description": "TSO/Spring基础窗口（天）"},
            {"rule_key": "pattern_extra_spring_days", "label": "Spring额外窗口", "category": "基础窗口", "value_type": "integer", "default": 8, "min": 0, "max": 30, "step": 1, "description": "Spring额外搜索扩展窗口（天）"},
            # Event switches
            {"rule_key": "enable_ps", "label": "启用PS", "category": "PS", "value_type": "boolean", "default": True, "description": "是否启用PS事件"},
            {"rule_key": "enable_sc", "label": "启用SC", "category": "SC", "value_type": "boolean", "default": True, "description": "是否启用SC事件"},
            {"rule_key": "enable_ar", "label": "启用AR", "category": "AR", "value_type": "boolean", "default": True, "description": "是否启用AR事件"},
            {"rule_key": "enable_st", "label": "启用ST", "category": "ST", "value_type": "boolean", "default": True, "description": "是否启用ST事件"},
            {"rule_key": "enable_tso", "label": "启用TSO", "category": "TSO", "value_type": "boolean", "default": True, "description": "是否启用TSO事件"},
            {"rule_key": "enable_spring", "label": "启用Spring", "category": "Spring", "value_type": "boolean", "default": True, "description": "是否启用Spring事件"},
            {"rule_key": "enable_sos", "label": "启用SOS", "category": "SOS", "value_type": "boolean", "default": True, "description": "是否启用SOS事件"},
            {"rule_key": "enable_joc", "label": "启用JOC", "category": "JOC", "value_type": "boolean", "default": True, "description": "是否启用JOC事件"},
            {"rule_key": "enable_lps", "label": "启用LPS", "category": "LPS", "value_type": "boolean", "default": True, "description": "是否启用LPS事件"},
            {"rule_key": "enable_psy", "label": "启用PSY", "category": "PSY", "value_type": "boolean", "default": True, "description": "是否启用PSY风险事件"},
            {"rule_key": "enable_bc", "label": "启用BC", "category": "BC", "value_type": "boolean", "default": True, "description": "是否启用BC风险事件"},
            {"rule_key": "enable_ar_d", "label": "启用AR(d)", "category": "AR(d)", "value_type": "boolean", "default": True, "description": "是否启用AR(d)风险事件"},
            {"rule_key": "enable_st_d", "label": "启用ST(d)", "category": "ST(d)", "value_type": "boolean", "default": True, "description": "是否启用ST(d)风险事件"},
            {"rule_key": "enable_utad", "label": "启用UTAD", "category": "UTAD", "value_type": "boolean", "default": True, "description": "是否启用UTAD风险事件"},
            {"rule_key": "enable_sow", "label": "启用SOW", "category": "SOW", "value_type": "boolean", "default": True, "description": "是否启用SOW风险事件"},
            {"rule_key": "enable_lpsy", "label": "启用LPSY", "category": "LPSY", "value_type": "boolean", "default": True, "description": "是否启用LPSY风险事件"},
            # SC / PS / AR / ST
            {"rule_key": "sc_anchor_tr_pos_max", "label": "SC锚点位置上限", "category": "SC", "value_type": "number", "default": 0.45, "min": 0.1, "max": 0.9, "step": 0.005, "description": "SC锚点TR位置最大值"},
            {"rule_key": "sc_anchor_close_open_max", "label": "SC锚点收开比上限", "category": "SC", "value_type": "number", "default": 1.02, "min": 0.9, "max": 1.15, "step": 0.001, "description": "SC锚点 close <= open * 阈值"},
            {"rule_key": "sc_anchor_vol_ratio_min", "label": "SC锚点量比下限", "category": "SC", "value_type": "number", "default": 1.2, "min": 0.5, "max": 3.0, "step": 0.01, "description": "SC锚点成交量相对阈值"},
            {"rule_key": "sc_close_near_low_ratio_max", "label": "SC收盘贴近低点上限", "category": "SC", "value_type": "number", "default": 0.38, "min": 0.05, "max": 0.9, "step": 0.005, "description": "SC收盘到低点距离占K线范围上限"},
            {"rule_key": "sc_tr_pos_max", "label": "SC位置上限", "category": "SC", "value_type": "number", "default": 0.55, "min": 0.15, "max": 0.95, "step": 0.005, "description": "SC最终判定TR位置上限"},
            {"rule_key": "sc_vol_ratio_min", "label": "SC量比下限", "category": "SC", "value_type": "number", "default": 1.05, "min": 0.5, "max": 3.0, "step": 0.01, "description": "SC最终判定量比下限"},
            {"rule_key": "ps_window_before_sc_days", "label": "PS向前搜索窗口", "category": "PS", "value_type": "integer", "default": 25, "min": 5, "max": 80, "step": 1, "description": "PS在SC前搜索天数"},
            {"rule_key": "ps_tr_pos_max", "label": "PS位置上限", "category": "PS", "value_type": "number", "default": 0.42, "min": 0.1, "max": 0.9, "step": 0.005, "description": "PS的TR位置上限"},
            {"rule_key": "ps_vol_ratio_min", "label": "PS量比下限", "category": "PS", "value_type": "number", "default": 1.08, "min": 0.5, "max": 3.0, "step": 0.01, "description": "PS量比下限"},
            {"rule_key": "ps_close_ma20_max", "label": "PS收盘相对MA20上限", "category": "PS", "value_type": "number", "default": 1.02, "min": 0.85, "max": 1.2, "step": 0.001, "description": "PS条件 close <= MA20 * 阈值"},
            {"rule_key": "ar_window_after_sc_days", "label": "AR向后搜索窗口", "category": "AR", "value_type": "integer", "default": 18, "min": 3, "max": 60, "step": 1, "description": "AR在SC后搜索天数"},
            {"rule_key": "ar_rebound_min", "label": "AR反弹下限", "category": "AR", "value_type": "number", "default": 1.05, "min": 1.0, "max": 1.5, "step": 0.001, "description": "AR反弹阈值：反弹高点 >= SC收盘 * 阈值"},
            {"rule_key": "st_window_after_ar_days", "label": "ST在AR后窗口", "category": "ST", "value_type": "integer", "default": 12, "min": 3, "max": 50, "step": 1, "description": "AR存在时ST搜索窗口"},
            {"rule_key": "st_window_after_sc_days", "label": "ST在SC后窗口", "category": "ST", "value_type": "integer", "default": 18, "min": 3, "max": 60, "step": 1, "description": "AR缺失时ST搜索窗口"},
            {"rule_key": "st_low_near_sc_tol", "label": "ST贴近SC低点容差", "category": "ST", "value_type": "number", "default": 0.04, "min": 0.0, "max": 0.25, "step": 0.001, "description": "ST低点接近SC低点的最大偏离"},
            {"rule_key": "st_vol_vs_sc_max", "label": "ST相对SC量比上限", "category": "ST", "value_type": "number", "default": 0.85, "min": 0.1, "max": 1.5, "step": 0.005, "description": "ST成交量 <= SC成交量 * 阈值"},
            # TSO / Spring / SOS / JOC / LPS
            {"rule_key": "tso_break_prior_low_max", "label": "TSO下破比例上限", "category": "TSO", "value_type": "number", "default": 0.99, "min": 0.85, "max": 1.05, "step": 0.001, "description": "TSO条件 low < prior_low * 阈值"},
            {"rule_key": "tso_close_reclaim_min", "label": "TSO收盘收复下限", "category": "TSO", "value_type": "number", "default": 1.0, "min": 0.9, "max": 1.1, "step": 0.001, "description": "TSO条件 close > prior_low * 阈值"},
            {"rule_key": "tso_vol_ratio_min", "label": "TSO量比下限", "category": "TSO", "value_type": "number", "default": 1.0, "min": 0.5, "max": 3.0, "step": 0.01, "description": "TSO量比下限"},
            {"rule_key": "spring_break_prior_low_max", "label": "Spring下破比例上限", "category": "Spring", "value_type": "number", "default": 0.985, "min": 0.8, "max": 1.05, "step": 0.001, "description": "Spring条件 low < prior_low * 阈值"},
            {"rule_key": "spring_close_reclaim_min", "label": "Spring收盘收复下限", "category": "Spring", "value_type": "number", "default": 1.0, "min": 0.9, "max": 1.1, "step": 0.001, "description": "Spring条件 close > prior_low * 阈值"},
            {"rule_key": "spring_vol_ratio_min", "label": "Spring量比下限", "category": "Spring", "value_type": "number", "default": 1.15, "min": 0.5, "max": 3.5, "step": 0.01, "description": "Spring量比下限"},
            {"rule_key": "sos_close_ma20_min", "label": "SOS收盘相对MA20下限", "category": "SOS", "value_type": "number", "default": 1.01, "min": 0.9, "max": 1.3, "step": 0.001, "description": "SOS条件 close > MA20 * 阈值"},
            {"rule_key": "sos_ret10_min", "label": "SOS十日涨幅下限", "category": "SOS", "value_type": "number", "default": 0.05, "min": -0.1, "max": 0.5, "step": 0.001, "description": "SOS条件 ret10 > 阈值"},
            {"rule_key": "sos_vol_ratio_min", "label": "SOS量比下限", "category": "SOS", "value_type": "number", "default": 1.05, "min": 0.5, "max": 3.5, "step": 0.01, "description": "SOS量比下限"},
            {"rule_key": "joc_close_break_prior_high_min", "label": "JOC突破比例下限", "category": "JOC", "value_type": "number", "default": 1.005, "min": 0.95, "max": 1.2, "step": 0.001, "description": "JOC条件 close >= prior_high * 阈值"},
            {"rule_key": "joc_vol_ratio_min", "label": "JOC量比下限", "category": "JOC", "value_type": "number", "default": 1.2, "min": 0.5, "max": 4.0, "step": 0.01, "description": "JOC量比下限"},
            {"rule_key": "lps_window_after_anchor_days", "label": "LPS搜索窗口", "category": "LPS", "value_type": "integer", "default": 12, "min": 3, "max": 40, "step": 1, "description": "LPS在SOS/JOC后搜索窗口"},
            {"rule_key": "lps_anchor_max_gap_days", "label": "LPS距锚点最大天数", "category": "LPS", "value_type": "integer", "default": 6, "min": 1, "max": 30, "step": 1, "description": "LPS距SOS/JOC锚点的最大间隔"},
            {"rule_key": "lps_close_ma20_min", "label": "LPS收盘相对MA20下限", "category": "LPS", "value_type": "number", "default": 0.995, "min": 0.85, "max": 1.2, "step": 0.001, "description": "LPS条件 close > MA20 * 阈值"},
            {"rule_key": "lps_vol_vs_prev5_max", "label": "LPS相对前5日均量上限", "category": "LPS", "value_type": "number", "default": 0.95, "min": 0.1, "max": 1.5, "step": 0.005, "description": "LPS成交量 <= 前5日均量 * 阈值"},
            # PSY / BC / AR(d) / ST(d)
            {"rule_key": "bc_anchor_tr_pos_min", "label": "BC锚点位置下限", "category": "BC", "value_type": "number", "default": 0.62, "min": 0.1, "max": 0.98, "step": 0.005, "description": "BC锚点TR位置最小值"},
            {"rule_key": "bc_anchor_close_open_min", "label": "BC锚点收开比下限", "category": "BC", "value_type": "number", "default": 0.98, "min": 0.8, "max": 1.2, "step": 0.001, "description": "BC锚点 close >= open * 阈值"},
            {"rule_key": "bc_anchor_vol_ratio_min", "label": "BC锚点量比下限", "category": "BC", "value_type": "number", "default": 1.2, "min": 0.5, "max": 4.0, "step": 0.01, "description": "BC锚点量比下限"},
            {"rule_key": "bc_anchor_high_prior_high_min", "label": "BC锚点突破比例下限", "category": "BC", "value_type": "number", "default": 0.995, "min": 0.9, "max": 1.2, "step": 0.001, "description": "BC锚点 high >= prior_high * 阈值"},
            {"rule_key": "bc_close_near_high_ratio_max", "label": "BC收盘贴近高点上限", "category": "BC", "value_type": "number", "default": 0.32, "min": 0.05, "max": 0.9, "step": 0.005, "description": "BC收盘到高点距离占K线范围上限"},
            {"rule_key": "bc_tr_pos_min", "label": "BC位置下限", "category": "BC", "value_type": "number", "default": 0.58, "min": 0.05, "max": 0.98, "step": 0.005, "description": "BC最终判定TR位置下限"},
            {"rule_key": "bc_vol_ratio_min", "label": "BC量比下限", "category": "BC", "value_type": "number", "default": 1.05, "min": 0.5, "max": 4.0, "step": 0.01, "description": "BC最终判定量比下限"},
            {"rule_key": "bc_high_recent_high_min", "label": "BC近期新高比例下限", "category": "BC", "value_type": "number", "default": 0.995, "min": 0.9, "max": 1.2, "step": 0.001, "description": "BC条件 high >= 最近高点 * 阈值"},
            {"rule_key": "psy_window_before_bc_days", "label": "PSY向前搜索窗口", "category": "PSY", "value_type": "integer", "default": 20, "min": 5, "max": 60, "step": 1, "description": "PSY在BC前搜索天数"},
            {"rule_key": "psy_tr_pos_min", "label": "PSY位置下限", "category": "PSY", "value_type": "number", "default": 0.62, "min": 0.1, "max": 0.98, "step": 0.005, "description": "PSY TR位置下限"},
            {"rule_key": "psy_vol_ratio_min", "label": "PSY量比下限", "category": "PSY", "value_type": "number", "default": 1.05, "min": 0.5, "max": 4.0, "step": 0.01, "description": "PSY量比下限"},
            {"rule_key": "psy_close_ma20_min", "label": "PSY收盘相对MA20下限", "category": "PSY", "value_type": "number", "default": 0.98, "min": 0.8, "max": 1.2, "step": 0.001, "description": "PSY条件 close >= MA20 * 阈值"},
            {"rule_key": "ard_window_after_bc_days", "label": "AR(d)向后搜索窗口", "category": "AR(d)", "value_type": "integer", "default": 18, "min": 3, "max": 60, "step": 1, "description": "AR(d)在BC后搜索天数"},
            {"rule_key": "ard_decline_close_bc_max", "label": "AR(d)回落比例上限", "category": "AR(d)", "value_type": "number", "default": 0.94, "min": 0.5, "max": 1.0, "step": 0.001, "description": "AR(d)条件 最低收盘 <= BC收盘 * 阈值"},
            {"rule_key": "std_window_after_bc_days", "label": "ST(d)搜索窗口", "category": "ST(d)", "value_type": "integer", "default": 24, "min": 3, "max": 80, "step": 1, "description": "ST(d)在BC后搜索窗口"},
            {"rule_key": "std_high_near_bc_tol", "label": "ST(d)贴近BC高点容差", "category": "ST(d)", "value_type": "number", "default": 0.04, "min": 0.0, "max": 0.3, "step": 0.001, "description": "ST(d)高点接近BC高点的最大偏离"},
            {"rule_key": "std_vol_vs_bc_max", "label": "ST(d)相对BC量比上限", "category": "ST(d)", "value_type": "number", "default": 0.9, "min": 0.1, "max": 2.0, "step": 0.005, "description": "ST(d)成交量 <= BC成交量 * 阈值"},
            {"rule_key": "std_close_high_max", "label": "ST(d)收盘贴高上限", "category": "ST(d)", "value_type": "number", "default": 0.985, "min": 0.7, "max": 1.1, "step": 0.001, "description": "ST(d)条件 close <= high * 阈值"},
            # UTAD / SOW / LPSY
            {"rule_key": "utad_high_break_prior_high_min", "label": "UTAD假突破比例下限", "category": "UTAD", "value_type": "number", "default": 1.01, "min": 0.95, "max": 1.3, "step": 0.001, "description": "UTAD条件 high >= prior_high * 阈值"},
            {"rule_key": "utad_close_back_below_prior_high_max", "label": "UTAD回落比例上限", "category": "UTAD", "value_type": "number", "default": 1.0, "min": 0.8, "max": 1.2, "step": 0.001, "description": "UTAD条件 close < prior_high * 阈值"},
            {"rule_key": "utad_upper_shadow_min", "label": "UTAD上影占比下限", "category": "UTAD", "value_type": "number", "default": 0.5, "min": 0.0, "max": 1.0, "step": 0.005, "description": "UTAD上影线占K线范围最小值"},
            {"rule_key": "utad_vol_ratio_min", "label": "UTAD量比下限", "category": "UTAD", "value_type": "number", "default": 1.2, "min": 0.5, "max": 4.0, "step": 0.01, "description": "UTAD量比下限"},
            {"rule_key": "sow_ret10_max", "label": "SOW十日涨幅上限", "category": "SOW", "value_type": "number", "default": -0.05, "min": -0.6, "max": 0.1, "step": 0.001, "description": "SOW条件 ret10 <= 阈值"},
            {"rule_key": "sow_close_ma20_max", "label": "SOW收盘相对MA20上限", "category": "SOW", "value_type": "number", "default": 0.995, "min": 0.7, "max": 1.2, "step": 0.001, "description": "SOW条件 close < MA20 * 阈值"},
            {"rule_key": "sow_vol_ratio_min", "label": "SOW量比下限", "category": "SOW", "value_type": "number", "default": 1.1, "min": 0.5, "max": 4.0, "step": 0.01, "description": "SOW量比下限"},
            {"rule_key": "lpsy_window_after_anchor_days", "label": "LPSY搜索窗口", "category": "LPSY", "value_type": "integer", "default": 16, "min": 3, "max": 80, "step": 1, "description": "LPSY在SOW/UTAD后搜索窗口"},
            {"rule_key": "lpsy_close_ma20_max", "label": "LPSY收盘相对MA20上限", "category": "LPSY", "value_type": "number", "default": 1.0, "min": 0.7, "max": 1.2, "step": 0.001, "description": "LPSY条件 close < MA20 * 阈值"},
            {"rule_key": "lpsy_short_window_days", "label": "LPSY短高点窗口", "category": "LPSY", "value_type": "integer", "default": 5, "min": 2, "max": 20, "step": 1, "description": "LPSY最近高点窗口（短）"},
            {"rule_key": "lpsy_long_window_days", "label": "LPSY长高点窗口", "category": "LPSY", "value_type": "integer", "default": 10, "min": 4, "max": 40, "step": 1, "description": "LPSY历史高点窗口（长）"},
            {"rule_key": "lpsy_lower_high_max", "label": "LPSY次高点比例上限", "category": "LPSY", "value_type": "number", "default": 0.99, "min": 0.7, "max": 1.1, "step": 0.001, "description": "LPSY条件 近期高点 <= 更早高点 * 阈值"},
        ]
        options: list[dict[str, Any]] = []
        for raw in specs:
            rule_key = str(raw.get("rule_key", "")).strip()
            if not rule_key:
                continue
            value_type = str(raw.get("value_type", "number")).strip().lower()
            if value_type not in {"number", "integer", "boolean"}:
                value_type = "number"
            min_value = raw.get("min")
            max_value = raw.get("max")
            default_value = raw.get("default")
            recommended_min = raw.get("recommended_min")
            recommended_max = raw.get("recommended_max")
            if value_type in {"number", "integer"} and isinstance(default_value, (int, float)):
                default_num = float(default_value)
                min_num = float(min_value) if isinstance(min_value, (int, float)) else None
                max_num = float(max_value) if isinstance(max_value, (int, float)) else None
                if recommended_min is None and recommended_max is None and min_num is not None and max_num is not None and max_num > min_num:
                    spread = (max_num - min_num) * 0.30
                    recommended_min = max(min_num, default_num - spread)
                    recommended_max = min(max_num, default_num + spread)
            options.append(
                {
                    "rule_key": rule_key,
                    "label": str(raw.get("label", "")).strip() or rule_key,
                    "description": str(raw.get("description", "")).strip(),
                    "category": str(raw.get("category", "")).strip() or "其他",
                    "value_type": value_type,
                    "min_value": min_value,
                    "max_value": max_value,
                    "step": raw.get("step"),
                    "recommended_min": recommended_min,
                    "recommended_max": recommended_max,
                    "risk_hint_low": str(raw.get("risk_hint_low", "")).strip()
                    or "低于推荐区间，可能放宽或扭曲判别灵敏度，请结合信号数量与回测稳定性评估。",
                    "risk_hint_high": str(raw.get("risk_hint_high", "")).strip()
                    or "高于推荐区间，可能导致条件过严或漏判，请结合胜率与覆盖率权衡。",
                    "default_value": default_value,
                }
            )
        return options

    def _default_event_judgment_rule_values(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for item in self._event_judgment_rule_options:
            rule_key = str(item.get("rule_key", "")).strip()
            if not rule_key:
                continue
            out.append(
                {
                    "rule_key": rule_key,
                    "value": item.get("default_value"),
                }
            )
        return out

    def _build_event_judgment_system_profiles(self) -> dict[str, dict[str, Any]]:
        now_text = self._now_datetime()
        default_rule_values = self._default_event_judgment_rule_values()
        return {
            "system_legacy_formula_v1": {
                "profile_id": "system_legacy_formula_v1",
                "name": "系统预设：经典综合判别",
                "description": "保持当前版本历史公式，兼容旧结果。",
                "score_mode": "legacy_formula",
                "is_system": True,
                "updated_at": now_text,
                "dimensions": [],
                "rule_values": [dict(item) for item in default_rule_values],
            },
            "system_3d_core_v1": {
                "profile_id": "system_3d_core_v1",
                "name": "系统预设：三维核心",
                "description": "背景 + 位置 + 量价 三维判别。",
                "score_mode": "dimension_weighted",
                "is_system": True,
                "updated_at": now_text,
                "dimensions": [
                    {
                        "dimension_id": "dim_background",
                        "label": "背景分",
                        "metric_key": "event_background_score",
                        "weight": 0.34,
                        "invert": False,
                        "enabled": True,
                    },
                    {
                        "dimension_id": "dim_position",
                        "label": "位置分",
                        "metric_key": "event_position_score",
                        "weight": 0.33,
                        "invert": False,
                        "enabled": True,
                    },
                    {
                        "dimension_id": "dim_vol_price",
                        "label": "量价分",
                        "metric_key": "event_vol_price_score",
                        "weight": 0.33,
                        "invert": False,
                        "enabled": True,
                    },
                ],
                "rule_values": [dict(item) for item in default_rule_values],
            },
            "system_6d_confirmation_v1": {
                "profile_id": "system_6d_confirmation_v1",
                "name": "系统预设：六维确认增强",
                "description": "背景/位置/量价/确认/语境/新鲜度 六维综合。",
                "score_mode": "dimension_weighted",
                "is_system": True,
                "updated_at": now_text,
                "dimensions": [
                    {
                        "dimension_id": "dim_background",
                        "label": "背景分",
                        "metric_key": "event_background_score",
                        "weight": 0.22,
                        "invert": False,
                        "enabled": True,
                    },
                    {
                        "dimension_id": "dim_position",
                        "label": "位置分",
                        "metric_key": "event_position_score",
                        "weight": 0.14,
                        "invert": False,
                        "enabled": True,
                    },
                    {
                        "dimension_id": "dim_vol_price",
                        "label": "量价分",
                        "metric_key": "event_vol_price_score",
                        "weight": 0.20,
                        "invert": False,
                        "enabled": True,
                    },
                    {
                        "dimension_id": "dim_confirmation",
                        "label": "确认分",
                        "metric_key": "event_confirmation_score",
                        "weight": 0.18,
                        "invert": False,
                        "enabled": True,
                    },
                    {
                        "dimension_id": "dim_context",
                        "label": "阶段语境分",
                        "metric_key": "phase_context_score",
                        "weight": 0.14,
                        "invert": False,
                        "enabled": True,
                    },
                    {
                        "dimension_id": "dim_recency",
                        "label": "新鲜度分",
                        "metric_key": "event_recency_score",
                        "weight": 0.12,
                        "invert": False,
                        "enabled": True,
                    },
                ],
                "rule_values": [dict(item) for item in default_rule_values],
            },
        }

    def _normalize_event_judgment_dimension(self, raw: Any, *, fallback_id: str) -> dict[str, Any] | None:
        if not isinstance(raw, dict):
            return None
        metric_key = str(raw.get("metric_key", "")).strip()
        supported_metrics = {str(item.get("metric_key", "")).strip() for item in self._event_judgment_metric_options}
        if metric_key not in supported_metrics:
            return None
        try:
            weight = float(raw.get("weight", 1.0))
        except Exception:
            weight = 1.0
        weight = max(0.0, min(10.0, weight))
        dimension_id = str(raw.get("dimension_id", "")).strip() or fallback_id
        label = str(raw.get("label", "")).strip() or metric_key
        invert = bool(raw.get("invert", False))
        enabled = bool(raw.get("enabled", True))
        return {
            "dimension_id": dimension_id[:64],
            "label": label[:64],
            "metric_key": metric_key,
            "weight": round(weight, 6),
            "invert": invert,
            "enabled": enabled,
        }

    def _normalize_event_judgment_rule_value(self, raw: Any) -> dict[str, Any] | None:
        payload = raw if isinstance(raw, dict) else {}
        rule_key = str(payload.get("rule_key", "")).strip()
        if not rule_key:
            return None
        option = self._event_judgment_rule_option_map.get(rule_key)
        if not isinstance(option, dict):
            return None
        value_type = str(option.get("value_type", "number")).strip().lower()
        default_value = option.get("default_value")
        candidate = payload.get("value", default_value)
        min_value = option.get("min_value")
        max_value = option.get("max_value")

        if value_type == "boolean":
            if isinstance(candidate, str):
                normalized_text = candidate.strip().lower()
                if normalized_text in {"1", "true", "yes", "y", "on"}:
                    normalized_value: bool | int | float = True
                elif normalized_text in {"0", "false", "no", "n", "off"}:
                    normalized_value = False
                else:
                    normalized_value = bool(default_value)
            else:
                normalized_value = bool(candidate)
            return {
                "rule_key": rule_key[:96],
                "value": bool(normalized_value),
            }

        if value_type == "integer":
            try:
                parsed_int = int(round(float(candidate)))
            except Exception:
                parsed_int = int(round(float(default_value or 0)))
            if isinstance(min_value, (int, float)):
                parsed_int = max(int(round(float(min_value))), parsed_int)
            if isinstance(max_value, (int, float)):
                parsed_int = min(int(round(float(max_value))), parsed_int)
            return {
                "rule_key": rule_key[:96],
                "value": int(parsed_int),
            }

        try:
            parsed_float = float(candidate)
        except Exception:
            parsed_float = float(default_value or 0.0)
        if isinstance(min_value, (int, float)):
            parsed_float = max(float(min_value), parsed_float)
        if isinstance(max_value, (int, float)):
            parsed_float = min(float(max_value), parsed_float)
        return {
            "rule_key": rule_key[:96],
            "value": round(float(parsed_float), 6),
        }

    def _normalize_event_judgment_rule_values(
        self,
        raw_values: Any,
        *,
        fallback_values: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        resolved: dict[str, dict[str, Any]] = {}
        for item in self._default_event_judgment_rule_values():
            normalized = self._normalize_event_judgment_rule_value(item)
            if normalized is None:
                continue
            resolved[str(normalized.get("rule_key", ""))] = normalized

        def merge(values: Any) -> None:
            if not isinstance(values, list):
                return
            for raw in values:
                item = raw
                if not isinstance(item, dict) and hasattr(item, "model_dump"):
                    try:
                        item = item.model_dump()  # type: ignore[assignment]
                    except Exception:
                        item = None
                normalized = self._normalize_event_judgment_rule_value(item)
                if normalized is None:
                    continue
                resolved[str(normalized.get("rule_key", ""))] = normalized

        merge(fallback_values)
        merge(raw_values)

        ordered: list[dict[str, Any]] = []
        for option in self._event_judgment_rule_options:
            rule_key = str(option.get("rule_key", "")).strip()
            if not rule_key:
                continue
            normalized = resolved.get(rule_key)
            if normalized is None:
                continue
            ordered.append(dict(normalized))
        return ordered

    def _normalize_event_judgment_profile(
        self,
        raw: Any,
        *,
        is_system: bool,
        fallback_profile_id: str,
        fallback_name: str,
        fallback_updated_at: str,
        fallback_rule_values: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        payload = raw if isinstance(raw, dict) else {}
        profile_id = str(payload.get("profile_id", "")).strip() or fallback_profile_id
        name = str(payload.get("name", "")).strip() or fallback_name
        description = str(payload.get("description", "")).strip()
        score_mode = str(payload.get("score_mode", "dimension_weighted")).strip().lower()
        if score_mode not in {"legacy_formula", "dimension_weighted"}:
            score_mode = "dimension_weighted"
        if score_mode == "legacy_formula":
            dimensions: list[dict[str, Any]] = []
        else:
            dimensions = []
            raw_dimensions = payload.get("dimensions")
            if isinstance(raw_dimensions, list):
                for index, item in enumerate(raw_dimensions):
                    normalized = self._normalize_event_judgment_dimension(
                        item,
                        fallback_id=f"dim_{index + 1}",
                    )
                    if normalized is None:
                        continue
                    dimensions.append(normalized)
            dimensions = dimensions[:24]
        rule_values = self._normalize_event_judgment_rule_values(
            payload.get("rule_values"),
            fallback_values=fallback_rule_values,
        )
        updated_at = str(payload.get("updated_at", "")).strip() or fallback_updated_at
        return {
            "profile_id": profile_id[:64],
            "name": name[:64],
            "description": description[:200],
            "score_mode": score_mode,
            "is_system": bool(is_system),
            "updated_at": updated_at,
            "dimensions": dimensions,
            "rule_values": rule_values,
        }

    def _event_judgment_profile_exists(self, profile_id: str) -> bool:
        profile_key = str(profile_id).strip()
        return profile_key in self._event_judgment_system_profiles or profile_key in self._event_judgment_custom_profiles

    def _resolve_event_judgment_profile(self, profile_id: str) -> dict[str, Any] | None:
        profile_key = str(profile_id).strip()
        if not profile_key:
            return None
        if profile_key in self._event_judgment_custom_profiles:
            return dict(self._event_judgment_custom_profiles[profile_key])
        if profile_key in self._event_judgment_system_profiles:
            return dict(self._event_judgment_system_profiles[profile_key])
        return None

    @staticmethod
    def _event_judgment_profile_hash(profile: dict[str, Any]) -> str:
        payload = {
            "profile_id": str(profile.get("profile_id", "")).strip(),
            "score_mode": str(profile.get("score_mode", "")).strip(),
            "dimensions": profile.get("dimensions") if isinstance(profile.get("dimensions"), list) else [],
            "rule_values": profile.get("rule_values") if isinstance(profile.get("rule_values"), list) else [],
        }
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]

    def _normalize_backtest_event_judgment_profile_snapshot(
        self,
        raw: Any,
        *,
        fallback_profile_id: str,
    ) -> dict[str, Any]:
        snapshot = raw if isinstance(raw, dict) else {}
        fallback_name = str(snapshot.get("name", "")).strip() or "事件判别模板快照"
        fallback_updated_at = str(snapshot.get("updated_at", "")).strip() or self._now_datetime()
        return self._normalize_event_judgment_profile(
            snapshot,
            is_system=bool(snapshot.get("is_system", False)),
            fallback_profile_id=fallback_profile_id,
            fallback_name=fallback_name,
            fallback_updated_at=fallback_updated_at,
            fallback_rule_values=self._default_event_judgment_rule_values(),
        )

    def _bind_backtest_event_judgment_profile(
        self,
        payload: BacktestRunRequest,
    ) -> tuple[BacktestRunRequest, dict[str, Any], str, str]:
        requested_profile_id = str(payload.event_judgment_profile_id or "").strip()
        snapshot_raw = payload.event_judgment_profile_snapshot
        profile: dict[str, Any] | None = None

        if isinstance(snapshot_raw, dict):
            fallback_profile_id = (
                requested_profile_id
                or str(snapshot_raw.get("profile_id", "")).strip()
                or self._default_event_judgment_profile_id()
            )
            profile = self._normalize_backtest_event_judgment_profile_snapshot(
                snapshot_raw,
                fallback_profile_id=fallback_profile_id,
            )
        elif requested_profile_id:
            profile = self._resolve_event_judgment_profile(requested_profile_id)
            if profile is None:
                raise BacktestValidationError(
                    "EVENT_JUDGMENT_PROFILE_NOT_FOUND",
                    f"事件判别模板不存在: {requested_profile_id}",
                )
        else:
            profile = self._active_event_judgment_profile()

        profile_id = str(profile.get("profile_id", "")).strip() or self._default_event_judgment_profile_id()
        profile_hash = self._event_judgment_profile_hash(profile)
        updates: dict[str, Any] = {}
        if str(payload.event_judgment_profile_id or "").strip() != profile_id:
            updates["event_judgment_profile_id"] = profile_id
        if str(payload.event_judgment_profile_hash or "").strip() != profile_hash:
            updates["event_judgment_profile_hash"] = profile_hash
        if payload.event_judgment_profile_snapshot != profile:
            updates["event_judgment_profile_snapshot"] = dict(profile)
        if updates:
            payload = payload.model_copy(update=updates, deep=True)
        return payload, dict(profile), profile_id, profile_hash

    def _active_event_judgment_profile(self) -> dict[str, Any]:
        active = self._resolve_event_judgment_profile(self._active_event_judgment_profile_id)
        if active is not None:
            return active
        fallback_id = self._default_event_judgment_profile_id()
        fallback = self._resolve_event_judgment_profile(fallback_id)
        if fallback is not None:
            self._active_event_judgment_profile_id = fallback_id
            return fallback
        first_system = next(iter(self._event_judgment_system_profiles.values()), None)
        if isinstance(first_system, dict):
            self._active_event_judgment_profile_id = str(first_system.get("profile_id") or fallback_id)
            return dict(first_system)
        self._active_event_judgment_profile_id = fallback_id
        return {
            "profile_id": fallback_id,
            "name": "系统预设",
            "description": "",
            "score_mode": "legacy_formula",
            "is_system": True,
            "updated_at": self._now_datetime(),
            "dimensions": [],
            "rule_values": self._default_event_judgment_rule_values(),
        }

    def _active_event_judgment_profile_hash(self) -> str:
        profile = self._active_event_judgment_profile()
        return self._event_judgment_profile_hash(profile)

    def _get_backtest_runtime_event_judgment_binding(self) -> dict[str, Any] | None:
        binding = getattr(self._backtest_runtime_context, "event_judgment_binding", None)
        return dict(binding) if isinstance(binding, dict) else None

    def _set_backtest_runtime_event_judgment_binding(
        self,
        *,
        profile: dict[str, Any],
        profile_id: str,
        profile_hash: str,
    ) -> None:
        setattr(
            self._backtest_runtime_context,
            "event_judgment_binding",
            {
                "profile": dict(profile),
                "profile_id": str(profile_id).strip(),
                "profile_hash": str(profile_hash).strip(),
            },
        )

    def _restore_backtest_runtime_event_judgment_binding(self, binding: dict[str, Any] | None) -> None:
        if binding is None:
            if hasattr(self._backtest_runtime_context, "event_judgment_binding"):
                delattr(self._backtest_runtime_context, "event_judgment_binding")
            return
        setattr(self._backtest_runtime_context, "event_judgment_binding", dict(binding))

    @contextmanager
    def _backtest_runtime_event_judgment_binding(
        self,
        *,
        profile: dict[str, Any],
        profile_id: str,
        profile_hash: str,
    ):
        previous = self._get_backtest_runtime_event_judgment_binding()
        self._set_backtest_runtime_event_judgment_binding(
            profile=profile,
            profile_id=profile_id,
            profile_hash=profile_hash,
        )
        try:
            yield
        finally:
            self._restore_backtest_runtime_event_judgment_binding(previous)

    def _all_event_judgment_profiles(self) -> list[dict[str, Any]]:
        profiles: list[dict[str, Any]] = []
        for profile in self._event_judgment_system_profiles.values():
            profiles.append(dict(profile))
        custom_items = sorted(
            self._event_judgment_custom_profiles.values(),
            key=lambda item: (
                str(item.get("updated_at", "")),
                str(item.get("name", "")),
            ),
            reverse=True,
        )
        for profile in custom_items:
            profiles.append(dict(profile))
        return profiles

    def _to_event_judgment_profile_model(self, payload: dict[str, Any]) -> EventJudgmentProfile:
        dimensions: list[EventJudgmentDimension] = []
        raw_dimensions = payload.get("dimensions")
        if isinstance(raw_dimensions, list):
            for item in raw_dimensions:
                if not isinstance(item, dict):
                    continue
                try:
                    dimensions.append(EventJudgmentDimension(**item))
                except Exception:
                    continue
        rule_values: list[EventJudgmentRuleValue] = []
        raw_rule_values = payload.get("rule_values")
        if isinstance(raw_rule_values, list):
            for item in raw_rule_values:
                if not isinstance(item, dict):
                    continue
                try:
                    rule_values.append(EventJudgmentRuleValue(**item))
                except Exception:
                    continue
        return EventJudgmentProfile(
            profile_id=str(payload.get("profile_id", "")).strip() or "unknown",
            name=str(payload.get("name", "")).strip() or "未命名",
            description=str(payload.get("description", "")).strip(),
            score_mode=(
                "legacy_formula"
                if str(payload.get("score_mode", "")).strip() == "legacy_formula"
                else "dimension_weighted"
            ),
            is_system=bool(payload.get("is_system", False)),
            updated_at=str(payload.get("updated_at", "")).strip() or self._now_datetime(),
            dimensions=dimensions,
            rule_values=rule_values,
        )

    def _build_event_judgment_catalog_response(self) -> EventJudgmentCatalogResponse:
        metrics = [
            EventJudgmentMetricOption(
                metric_key=str(item.get("metric_key", "")).strip(),
                label=str(item.get("label", "")).strip() or str(item.get("metric_key", "")).strip(),
                description=str(item.get("description", "")).strip(),
            )
            for item in self._event_judgment_metric_options
            if str(item.get("metric_key", "")).strip()
        ]
        rule_options = [
            EventJudgmentRuleOption(
                rule_key=str(item.get("rule_key", "")).strip(),
                label=str(item.get("label", "")).strip() or str(item.get("rule_key", "")).strip(),
                description=str(item.get("description", "")).strip(),
                category=str(item.get("category", "")).strip(),
                value_type=(
                    "integer"
                    if str(item.get("value_type", "")).strip() == "integer"
                    else "boolean"
                    if str(item.get("value_type", "")).strip() == "boolean"
                    else "number"
                ),
                min_value=(
                    float(item.get("min_value"))
                    if isinstance(item.get("min_value"), (int, float))
                    else None
                ),
                max_value=(
                    float(item.get("max_value"))
                    if isinstance(item.get("max_value"), (int, float))
                    else None
                ),
                step=(
                    float(item.get("step"))
                    if isinstance(item.get("step"), (int, float))
                    else None
                ),
                recommended_min=(
                    float(item.get("recommended_min"))
                    if isinstance(item.get("recommended_min"), (int, float))
                    else None
                ),
                recommended_max=(
                    float(item.get("recommended_max"))
                    if isinstance(item.get("recommended_max"), (int, float))
                    else None
                ),
                risk_hint_low=str(item.get("risk_hint_low", "")).strip(),
                risk_hint_high=str(item.get("risk_hint_high", "")).strip(),
                default_value=item.get("default_value"),
            )
            for item in self._event_judgment_rule_options
            if str(item.get("rule_key", "")).strip()
        ]
        profiles = [self._to_event_judgment_profile_model(item) for item in self._all_event_judgment_profiles()]
        active_id = str(self._active_event_judgment_profile_id or "").strip()
        if not active_id or not any(item.profile_id == active_id for item in profiles):
            fallback_id = self._default_event_judgment_profile_id()
            if any(item.profile_id == fallback_id for item in profiles):
                active_id = fallback_id
            elif profiles:
                active_id = profiles[0].profile_id
            else:
                active_id = fallback_id
            self._active_event_judgment_profile_id = active_id
        return EventJudgmentCatalogResponse(
            active_profile_id=active_id,
            metric_options=metrics,
            rule_options=rule_options,
            profiles=profiles,
        )

    def list_event_judgment_profiles(self) -> EventJudgmentCatalogResponse:
        return self._build_event_judgment_catalog_response()

    @state_locked
    def upsert_event_judgment_profile(self, payload: EventJudgmentProfileUpsertRequest) -> EventJudgmentProfile:
        target_profile_id = str(payload.profile_id or "").strip()
        if target_profile_id and target_profile_id in self._event_judgment_system_profiles:
            raise BacktestValidationError("EVENT_JUDGMENT_SYSTEM_READONLY", "系统预设不允许直接覆盖，请另存为自定义模板。")

        source_profile = self._resolve_event_judgment_profile(target_profile_id) if target_profile_id else None
        if target_profile_id and source_profile is None:
            raise BacktestValidationError("EVENT_JUDGMENT_PROFILE_NOT_FOUND", f"事件判别模板不存在: {target_profile_id}")

        now_text = self._now_datetime()
        normalized_dimensions: list[dict[str, Any]] = []
        for index, item in enumerate(payload.dimensions):
            normalized = self._normalize_event_judgment_dimension(
                item.model_dump(),
                fallback_id=f"dim_{index + 1}",
            )
            if normalized is None:
                continue
            normalized_dimensions.append(normalized)
        if not normalized_dimensions:
            raise BacktestValidationError("EVENT_JUDGMENT_DIMENSIONS_EMPTY", "至少需要一个有效判别维度。")
        source_rule_values = (
            source_profile.get("rule_values")
            if isinstance(source_profile, dict) and isinstance(source_profile.get("rule_values"), list)
            else None
        )
        incoming_rule_values = (
            [item.model_dump() for item in payload.rule_values]
            if isinstance(payload.rule_values, list)
            else None
        )
        normalized_rule_values = self._normalize_event_judgment_rule_values(
            incoming_rule_values,
            fallback_values=source_rule_values if isinstance(source_rule_values, list) else None,
        )

        if target_profile_id and target_profile_id in self._event_judgment_custom_profiles:
            profile_id = target_profile_id
        else:
            profile_id = f"ejp_{int(time.time() * 1000):x}_{uuid4().hex[:6]}"

        next_payload = self._normalize_event_judgment_profile(
            {
                "profile_id": profile_id,
                "name": str(payload.name).strip(),
                "description": str(payload.description or "").strip(),
                "score_mode": "dimension_weighted",
                "updated_at": now_text,
                "dimensions": normalized_dimensions,
                "rule_values": normalized_rule_values,
            },
            is_system=False,
            fallback_profile_id=profile_id,
            fallback_name=str(payload.name).strip() or "自定义模板",
            fallback_updated_at=now_text,
            fallback_rule_values=source_rule_values if isinstance(source_rule_values, list) else None,
        )
        self._event_judgment_custom_profiles[profile_id] = next_payload
        if bool(payload.make_active):
            self._active_event_judgment_profile_id = profile_id
            self._signals_cache.clear()
        self._persist_app_state()
        return self._to_event_judgment_profile_model(next_payload)

    @state_locked
    def apply_event_judgment_profile(self, profile_id: str) -> EventJudgmentCatalogResponse:
        normalized_profile_id = str(profile_id or "").strip()
        if not normalized_profile_id:
            raise BacktestValidationError("EVENT_JUDGMENT_PROFILE_NOT_FOUND", "事件判别模板不存在。")
        if not self._event_judgment_profile_exists(normalized_profile_id):
            raise BacktestValidationError("EVENT_JUDGMENT_PROFILE_NOT_FOUND", f"事件判别模板不存在: {normalized_profile_id}")
        self._active_event_judgment_profile_id = normalized_profile_id
        self._signals_cache.clear()
        self._persist_app_state()
        return self._build_event_judgment_catalog_response()

    @state_locked
    def delete_event_judgment_profile(self, profile_id: str) -> EventJudgmentProfileDeleteResponse:
        normalized_profile_id = str(profile_id or "").strip()
        if not normalized_profile_id:
            raise BacktestValidationError("EVENT_JUDGMENT_PROFILE_NOT_FOUND", "事件判别模板不存在。")
        if normalized_profile_id in self._event_judgment_system_profiles:
            raise BacktestValidationError("EVENT_JUDGMENT_SYSTEM_READONLY", "系统预设不允许删除。")
        if normalized_profile_id not in self._event_judgment_custom_profiles:
            raise BacktestValidationError("EVENT_JUDGMENT_PROFILE_NOT_FOUND", f"事件判别模板不存在: {normalized_profile_id}")
        del self._event_judgment_custom_profiles[normalized_profile_id]
        if self._active_event_judgment_profile_id == normalized_profile_id:
            self._active_event_judgment_profile_id = self._default_event_judgment_profile_id()
            self._signals_cache.clear()
        self._persist_app_state()
        return EventJudgmentProfileDeleteResponse(success=True, profile_id=normalized_profile_id)

    def list_strategies(self) -> StrategyCatalogResponse:
        items: list[StrategyDescriptor] = []
        for descriptor in self._strategy_registry.list():
            items.append(self._to_strategy_descriptor_model(descriptor))
        return StrategyCatalogResponse(items=items)

    @staticmethod
    def _to_strategy_descriptor_model(descriptor: Any) -> StrategyDescriptor:
        return StrategyDescriptor(
            strategy_id=str(descriptor.strategy_id),  # type: ignore[arg-type]
            name=str(descriptor.name),
            version=str(descriptor.version),
            enabled=bool(descriptor.enabled),
            is_default=bool(descriptor.is_default),
            capabilities=StrategyCapabilities(
                supports_matrix=bool(descriptor.capabilities.supports_matrix),
                supports_signal_age_filter=bool(descriptor.capabilities.supports_signal_age_filter),
                supports_entry_delay=bool(descriptor.capabilities.supports_entry_delay),
            ),
            description=str(getattr(descriptor, "description", "") or ""),
            strategy_playbook=dict(getattr(descriptor, "playbook", {}) or {}),
            strategy_params_schema=dict(descriptor.params_schema),
            strategy_params_defaults=dict(descriptor.default_params),
        )

    def update_strategy(
        self,
        *,
        strategy_id: str,
        enabled: bool | None = None,
        is_default: bool | None = None,
        version: str | None = None,
    ) -> StrategyDescriptor:
        try:
            descriptor = self._strategy_registry.update_descriptor(
                strategy_id=strategy_id,
                enabled=enabled,
                is_default=is_default,
                version=version,
            )
        except ValueError as exc:
            raise BacktestValidationError("STRATEGY_NOT_FOUND", str(exc)) from exc
        return self._to_strategy_descriptor_model(descriptor)

    @classmethod
    def _resolve_app_state_path(cls, app_state_path: str | None = None) -> Path:
        if app_state_path and str(app_state_path).strip():
            return cls._resolve_user_path(app_state_path)
        env_value = os.getenv("TDX_TREND_APP_STATE_PATH", "").strip()
        if env_value:
            return cls._resolve_user_path(env_value)
        return Path.home() / ".tdx-trend" / "app_state.json"

    @classmethod
    def _resolve_wyckoff_event_store_path(cls) -> Path:
        env_value = os.getenv("TDX_TREND_WYCKOFF_STORE_PATH", "").strip()
        if env_value:
            return cls._resolve_user_path(env_value)
        return Path.home() / ".tdx-trend" / "wyckoff_events.sqlite"

    @classmethod
    def _resolve_backtest_task_state_path(cls) -> Path:
        env_value = os.getenv("TDX_TREND_BACKTEST_TASK_STATE_PATH", "").strip()
        if env_value:
            return cls._resolve_user_path(env_value)
        return Path.home() / ".tdx-trend" / "backtest_tasks.json"

    @classmethod
    def _resolve_backtest_plateau_task_state_path(cls) -> Path:
        env_value = os.getenv("TDX_TREND_BACKTEST_PLATEAU_TASK_STATE_PATH", "").strip()
        if env_value:
            return cls._resolve_user_path(env_value)
        return Path.home() / ".tdx-trend" / "backtest_plateau_tasks.json"

    @classmethod
    def _resolve_backtest_plateau_detail_store_dir(cls) -> Path:
        env_value = os.getenv("TDX_TREND_BACKTEST_PLATEAU_DETAIL_STORE_DIR", "").strip()
        if env_value:
            return cls._resolve_user_path(env_value)
        return Path.home() / ".tdx-trend" / "backtest-plateau-details"

    @classmethod
    def _resolve_backtest_input_pool_cache_dir(cls) -> Path:
        env_value = os.getenv("TDX_TREND_BACKTEST_INPUT_POOL_CACHE_DIR", "").strip()
        if env_value:
            return cls._resolve_user_path(env_value)
        return Path.home() / ".tdx-trend" / "backtest-input-cache"

    @classmethod
    def _resolve_screener_result_cache_dir(cls) -> Path:
        env_value = os.getenv("TDX_TREND_SCREENER_RESULT_CACHE_DIR", "").strip()
        if env_value:
            return cls._resolve_user_path(env_value)
        return Path.home() / ".tdx-trend" / "screener-result-cache"

    @classmethod
    def _resolve_signals_cache_dir(cls) -> Path:
        env_value = os.getenv("TDX_TREND_SIGNALS_CACHE_DIR", "").strip()
        if env_value:
            return cls._resolve_user_path(env_value)
        return Path.home() / ".tdx-trend" / "signals-cache"

    @classmethod
    def _resolve_backtest_trend_filter_cache_dir(cls) -> Path:
        env_value = os.getenv("TDX_TREND_BACKTEST_TREND_FILTER_CACHE_DIR", "").strip()
        if env_value:
            return cls._resolve_user_path(env_value)
        return Path.home() / ".tdx-trend" / "backtest-trend-filter-cache"

    @classmethod
    def _resolve_backtest_result_cache_dir(cls) -> Path:
        env_value = os.getenv("TDX_TREND_BACKTEST_RESULT_CACHE_DIR", "").strip()
        if env_value:
            return cls._resolve_user_path(env_value)
        return Path.home() / ".tdx-trend" / "backtest-result-cache"

    @classmethod
    def _resolve_backtest_signal_matrix_cache_dir(cls) -> Path:
        env_value = os.getenv("TDX_TREND_BACKTEST_SIGNAL_MATRIX_CACHE_DIR", "").strip()
        if env_value:
            return cls._resolve_user_path(env_value)
        return Path.home() / ".tdx-trend" / "backtest-signal-matrix-cache"

    @classmethod
    def _resolve_backtest_report_store_dir(cls) -> Path:
        env_value = os.getenv("TDX_TREND_BACKTEST_REPORT_STORE_DIR", "").strip()
        if env_value:
            return cls._resolve_user_path(env_value)
        return Path.home() / ".tdx-trend" / "backtest-reports"

    @classmethod
    def _resolve_cross_validate_history_dir(cls) -> Path:
        env_value = os.getenv("TDX_TREND_CROSS_VALIDATE_HISTORY_DIR", "").strip()
        if env_value:
            return cls._resolve_user_path(env_value)
        return Path.home() / ".tdx-trend" / "cross-validate-history"

    @staticmethod
    def _max_persisted_screener_runs() -> int:
        raw = os.getenv("TDX_TREND_MAX_PERSISTED_SCREENER_RUNS", "").strip()
        if not raw:
            return 12
        try:
            return max(1, min(200, int(raw)))
        except Exception:
            return 12

    def _serialize_screener_runs_for_state(self) -> dict[str, dict[str, Any]]:
        if not self._run_store:
            return {}
        limit = self._max_persisted_screener_runs()
        ordered_runs = sorted(self._run_store.values(), key=lambda item: item.created_at)
        selected_runs = ordered_runs[-limit:]
        out: dict[str, dict[str, Any]] = {}
        for run in selected_runs:
            out[str(run.run_id)] = run.model_dump(exclude_none=True)
        return out

    def _restore_screener_runs_from_state(self, raw_runs: object) -> None:
        if not isinstance(raw_runs, dict):
            return
        restored_runs: list[ScreenerRunDetail] = []
        for run_id, item in raw_runs.items():
            if not isinstance(item, dict):
                continue
            try:
                detail = ScreenerRunDetail(**item)
            except Exception:
                continue
            if str(detail.run_id).strip() != str(run_id).strip():
                detail = detail.model_copy(update={"run_id": str(run_id)})
            restored_runs.append(detail)
        if not restored_runs:
            self._run_store = {}
            self._latest_rows = {}
            return
        restored_runs.sort(key=lambda item: item.created_at)
        self._run_store = {}
        self._latest_rows = {}
        for detail in restored_runs:
            self._store_screener_run_detail(detail, persist=False)

    def _build_app_state_payload(self) -> dict[str, object]:
        return {
            "schema_version": self._APP_STATE_SCHEMA_VERSION,
            "config": self._config.model_dump(),
            "screener_runs": self._serialize_screener_runs_for_state(),
            "ai_records": [item.model_dump() for item in self._ai_record_store],
            "annotations": {symbol: item.model_dump() for symbol, item in self._annotation_store.items()},
            "daily_reviews": {day: item.model_dump() for day, item in self._daily_review_store.items()},
            "weekly_reviews": {week: item.model_dump() for week, item in self._weekly_review_store.items()},
            "signal_etf_backtests": self._signal_etf_backtest_store,
            "review_tags": {
                "emotion": [item.model_dump() for item in self._review_tags.get("emotion", [])],
                "reason": [item.model_dump() for item in self._review_tags.get("reason", [])],
            },
            "fill_tags": {order_id: item.model_dump() for order_id, item in self._fill_tag_store.items()},
            "event_judgment": {
                "active_profile_id": str(self._active_event_judgment_profile_id or "").strip()
                or self._default_event_judgment_profile_id(),
                "custom_profiles": list(self._event_judgment_custom_profiles.values()),
            },
            "audit": {
                "updated_at": self._now_datetime(),
            },
        }

    def _write_app_state_payload(self, payload: dict[str, object]) -> None:
        write_state(self._app_state_path, payload)

    def _persist_app_state(self) -> None:
        with self._lock:
            try:
                self._write_app_state_payload(self._build_app_state_payload())
            except (OSError, StatePersistenceError) as exc:
                if self._app_state_path.exists():
                    self._load_or_init_app_state()
                raise StatePersistenceError("保存失败，请检查磁盘空间和目录权限后重试。") from exc

    def _load_or_init_app_state(self) -> None:
        if not self._app_state_path.exists():
            self._persist_app_state()
            return
        try:
            raw = read_state(self._app_state_path)
            for field_name, field_type in {
                'config': dict, 'annotations': dict, 'ai_records': list,
                'daily_reviews': dict, 'weekly_reviews': dict, 'signal_etf_backtests': dict,
                'review_tags': dict, 'fill_tags': dict, 'event_judgment': dict,
            }.items():
                if field_name in raw and not isinstance(raw[field_name], field_type):
                    raise ValueError(f'invalid state field: {field_name}')
            default_config = self._default_config()
            config_raw = raw.get("config")
            if isinstance(config_raw, dict):
                merged = {**default_config.model_dump(), **config_raw}
                while True:
                    try:
                        self._config = AppConfig(**merged)
                        break
                    except ValidationError as exc:
                        invalid_fields = {item["loc"][0] for item in exc.errors()}
                        for name in invalid_fields:
                            if name not in AppConfig.model_fields:
                                raise
                            merged[name] = getattr(default_config, name)
                            warning = f"配置字段 {name} 无效，已使用默认值；原文件与其他记录已保留。"
                            if warning not in self._state_warnings:
                                self._state_warnings.append(warning)
                                logging.getLogger(__name__).warning(warning)
            self._restore_screener_runs_from_state(raw.get("screener_runs"))
            annotations_raw = raw.get("annotations")
            if isinstance(annotations_raw, dict):
                restored_annotations: dict[str, StockAnnotation] = {}
                for symbol, item in annotations_raw.items():
                    if not isinstance(item, dict):
                        continue
                    try:
                        restored_annotations[str(symbol)] = StockAnnotation(**item)
                    except Exception:
                        continue
                self._annotation_store = restored_annotations
            ai_records_raw = raw.get("ai_records")
            if isinstance(ai_records_raw, list):
                restored_records: list[AIAnalysisRecord] = []
                for item in ai_records_raw:
                    if not isinstance(item, dict):
                        continue
                    try:
                        restored_records.append(AIAnalysisRecord(**item))
                    except Exception:
                        continue
                self._ai_record_store = restored_records
            daily_reviews_raw = raw.get("daily_reviews")
            if isinstance(daily_reviews_raw, dict):
                restored_daily: dict[str, DailyReviewRecord] = {}
                for day, item in daily_reviews_raw.items():
                    if not isinstance(item, dict):
                        continue
                    try:
                        restored_daily[str(day)] = DailyReviewRecord(**item)
                    except Exception:
                        continue
                self._daily_review_store = restored_daily
            weekly_reviews_raw = raw.get("weekly_reviews")
            if isinstance(weekly_reviews_raw, dict):
                restored_weekly: dict[str, WeeklyReviewRecord] = {}
                for week, item in weekly_reviews_raw.items():
                    if not isinstance(item, dict):
                        continue
                    try:
                        restored_weekly[str(week)] = WeeklyReviewRecord(**item)
                    except Exception:
                        continue
                self._weekly_review_store = restored_weekly
            signal_etf_raw = raw.get("signal_etf_backtests")
            if isinstance(signal_etf_raw, dict):
                restored_signal_etf: dict[str, dict[str, Any]] = {}
                for record_id, item in signal_etf_raw.items():
                    if not isinstance(item, dict):
                        continue
                    normalized_record_id = str(record_id).strip()
                    if not normalized_record_id:
                        continue
                    payload = dict(item)
                    payload["record_id"] = normalized_record_id
                    restored_signal_etf[normalized_record_id] = payload
                self._signal_etf_backtest_store = restored_signal_etf
            review_tags_raw = raw.get("review_tags")
            if isinstance(review_tags_raw, dict):
                merged_tags = self._default_review_tags()
                for tag_type in ("emotion", "reason"):
                    values = review_tags_raw.get(tag_type)
                    if not isinstance(values, list):
                        continue
                    restored_tags: list[ReviewTag] = []
                    for item in values:
                        if not isinstance(item, dict):
                            continue
                        try:
                            restored_tags.append(ReviewTag(**item))
                        except Exception:
                            continue
                    merged_tags[tag_type] = restored_tags
                self._review_tags = merged_tags
            fill_tags_raw = raw.get("fill_tags")
            if isinstance(fill_tags_raw, dict):
                restored_fill_tags: dict[str, TradeFillTagAssignment] = {}
                for order_id, item in fill_tags_raw.items():
                    if not isinstance(item, dict):
                        continue
                    try:
                        restored_fill_tags[str(order_id)] = TradeFillTagAssignment(**item)
                    except Exception:
                        continue
                self._fill_tag_store = restored_fill_tags
            event_judgment_raw = raw.get("event_judgment")
            if isinstance(event_judgment_raw, dict):
                custom_profiles_raw = event_judgment_raw.get("custom_profiles")
                restored_custom_profiles: dict[str, dict[str, Any]] = {}
                if isinstance(custom_profiles_raw, list):
                    for item in custom_profiles_raw:
                        normalized = self._normalize_event_judgment_profile(
                            item,
                            is_system=False,
                            fallback_profile_id=f"ejp_{uuid4().hex[:12]}",
                            fallback_name="自定义模板",
                            fallback_updated_at=self._now_datetime(),
                        )
                        profile_id = str(normalized.get("profile_id", "")).strip()
                        if not profile_id:
                            continue
                        restored_custom_profiles[profile_id] = normalized
                self._event_judgment_custom_profiles = restored_custom_profiles
                active_profile_id = str(event_judgment_raw.get("active_profile_id", "")).strip()
                if self._event_judgment_profile_exists(active_profile_id):
                    self._active_event_judgment_profile_id = active_profile_id
                else:
                    self._active_event_judgment_profile_id = self._default_event_judgment_profile_id()
        except StateLoadError:
            raise
        except (ValueError, TypeError, KeyError) as exc:
            raise StateLoadError(f"应用状态加载失败，原文件已保留：{self._app_state_path}") from exc

    @staticmethod
    def _default_config() -> AppConfig:
        return AppConfig(
            tdx_data_path=r"D:\new_tdx\vipdoc",
            market_data_source="tdx_then_akshare",
            akshare_cache_dir=str(Path.home() / ".tdx-trend" / "akshare" / "daily"),
            markets=["sh", "sz"],
            return_window_days=40,
            candles_window_bars=120,
            backtest_matrix_engine_enabled=True,
            backtest_default_execution_path_preference="legacy",
            backtest_plateau_workers=4,
            top_n=500,
            turnover_threshold=0.05,
            amount_threshold=5e8,
            amplitude_threshold=0.03,
            initial_capital=1_000_000,
            ai_provider="openai",
            ai_timeout_sec=10,
            ai_retry_count=2,
            api_key="",
            api_key_path=r"%USERPROFILE%\.tdx-trend\app.config.json",
            ai_providers=[
                AIProviderConfig(
                    id="openai",
                    label="OpenAI",
                    base_url="https://api.openai.com/v1",
                    model="gpt-4o-mini",
                    api_key="",
                    api_key_path=r"%USERPROFILE%\.tdx-trend\openai.key",
                    enabled=True,
                ),
            ],
            ai_sources=[
                AISourceConfig(
                    id="eastmoney",
                    name="东方财富新闻",
                    url="https://finance.eastmoney.com/",
                    enabled=True,
                ),
            ],
        )

    @staticmethod
    def _now_date() -> str:
        return datetime.now().strftime("%Y-%m-%d")

    @staticmethod
    def _now_datetime() -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def _bump_wyckoff_metric(self, key: str, delta: int = 1) -> None:
        with self._wyckoff_metrics_lock:
            current = int(self._wyckoff_metrics.get(key, 0) or 0)
            self._wyckoff_metrics[key] = current + int(delta)

    def _set_wyckoff_metric(self, key: str, value: object) -> None:
        with self._wyckoff_metrics_lock:
            self._wyckoff_metrics[key] = value

    def _snapshot_wyckoff_metrics(self) -> dict[str, object]:
        with self._wyckoff_metrics_lock:
            return dict(self._wyckoff_metrics)

    def _record_wyckoff_snapshot_read_latency(self, duration_ms: float) -> None:
        if not math.isfinite(duration_ms):
            return
        with self._wyckoff_metrics_lock:
            current_reads = int(self._wyckoff_metrics.get("snapshot_reads", 0) or 0)
            current_total = float(self._wyckoff_metrics.get("snapshot_read_ms_total", 0.0) or 0.0)
            self._wyckoff_metrics["snapshot_reads"] = current_reads + 1
            self._wyckoff_metrics["snapshot_read_ms_total"] = current_total + max(0.0, float(duration_ms))

    @staticmethod
    def _is_snapshot_score_outlier(raw: object) -> bool:
        try:
            value = float(raw)
        except Exception:
            return True
        if not math.isfinite(value):
            return True
        return (value < 0.0) or (value > 100.0)

    def _inspect_wyckoff_snapshot_quality(
        self,
        snapshot: dict[str, object],
        *,
        trade_date: str,
    ) -> dict[str, int]:
        has_event_rows = False
        has_chain_rows = False
        date_misaligned = 0

        events = snapshot.get("events")
        if isinstance(events, list):
            for item in events:
                if str(item).strip():
                    has_event_rows = True
                    break

        risk_events = snapshot.get("risk_events")
        if isinstance(risk_events, list):
            for item in risk_events:
                if str(item).strip():
                    has_event_rows = True
                    break

        event_chain = snapshot.get("event_chain")
        if isinstance(event_chain, list):
            for row in event_chain:
                if not isinstance(row, dict):
                    continue
                code_text = str(row.get("event", "")).strip()
                date_text = str(row.get("date", "")).strip()
                if not code_text:
                    continue
                has_chain_rows = True
                if not date_text:
                    date_misaligned = 1
                    break
                parsed = self._parse_date(date_text)
                if parsed is None:
                    date_misaligned = 1
                    break
                if trade_date and date_text > trade_date:
                    date_misaligned = 1
                    break

        event_dates = snapshot.get("event_dates")
        if isinstance(event_dates, dict):
            for raw_date in event_dates.values():
                date_text = str(raw_date).strip()
                if not date_text:
                    continue
                parsed = self._parse_date(date_text)
                if parsed is None:
                    date_misaligned = 1
                    break
                if trade_date and date_text > trade_date:
                    date_misaligned = 1
                    break

        score_fields = (
            "entry_quality_score",
            "event_strength_score",
            "phase_score",
            "structure_score",
            "trend_score",
            "volatility_score",
        )
        score_outlier = 0
        for field in score_fields:
            if self._is_snapshot_score_outlier(snapshot.get(field, 0.0)):
                score_outlier = 1
                break

        return {
            "empty_events": 0 if (has_event_rows or has_chain_rows) else 1,
            "score_outliers": score_outlier,
            "date_misaligned": date_misaligned,
        }

    def _record_wyckoff_snapshot_quality(self, quality_flags: dict[str, int]) -> None:
        empty_events = int(max(0, quality_flags.get("empty_events", 0)))
        score_outliers = int(max(0, quality_flags.get("score_outliers", 0)))
        date_misaligned = int(max(0, quality_flags.get("date_misaligned", 0)))
        if empty_events > 0:
            self._bump_wyckoff_metric("quality_empty_events", empty_events)
        if score_outliers > 0:
            self._bump_wyckoff_metric("quality_score_outliers", score_outliers)
        if date_misaligned > 0:
            self._bump_wyckoff_metric("quality_date_misaligned", date_misaligned)

    @staticmethod
    def _days_ago(days: int) -> str:
        return (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    @staticmethod
    def _default_review_tags() -> dict[ReviewTagType, list[ReviewTag]]:
        created_at = "2026-01-01 00:00:00"
        emotion_rows = [
            ("emotion-01", "冲动追高", "red"),
            ("emotion-02", "恐慌割肉", "volcano"),
            ("emotion-03", "理性建仓", "blue"),
            ("emotion-04", "波段操作", "purple"),
            ("emotion-05", "止盈离场", "green"),
            ("emotion-06", "止损离场", "orange"),
        ]
        reason_rows = [
            ("reason-01", "财报利好", "geekblue"),
            ("reason-02", "政策利好", "magenta"),
            ("reason-03", "技术突破", "cyan"),
            ("reason-04", "板块轮动", "gold"),
            ("reason-05", "资金需求", "lime"),
            ("reason-06", "止损", "volcano"),
            ("reason-07", "止盈", "green"),
        ]
        return {
            "emotion": [
                ReviewTag(id=tag_id, name=name, color=color, created_at=created_at)
                for tag_id, name, color in emotion_rows
            ],
            "reason": [
                ReviewTag(id=tag_id, name=name, color=color, created_at=created_at)
                for tag_id, name, color in reason_rows
            ],
        }

    @staticmethod
    def _resolve_week_range(week_label: str) -> tuple[str, str]:
        match = re.match(r"^(\d{4})-W(\d{2})$", week_label)
        if not match:
            raise ValueError("week_label must be YYYY-Www")
        year = int(match.group(1))
        week = int(match.group(2))
        start = datetime.fromisocalendar(year, week, 1).strftime("%Y-%m-%d")
        end = datetime.fromisocalendar(year, week, 7).strftime("%Y-%m-%d")
        return start, end

    @staticmethod
    def _unique_ordered(values: list[str]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for value in values:
            token = str(value).strip()
            if not token or token in seen:
                continue
            seen.add(token)
            result.append(token)
        return result

    def _find_review_tag(self, tag_type: ReviewTagType, tag_id: str) -> ReviewTag | None:
        for item in self._review_tags.get(tag_type, []):
            if item.id == tag_id:
                return item
        return None

    @staticmethod
    def _hash_seed(text: str) -> int:
        return sum(ord(c) for c in text)

    def _default_ai_records(self) -> list[AIAnalysisRecord]:
        return [
            AIAnalysisRecord(
                provider="openai",
                symbol="sz300750",
                name="宁德时代",
                fetched_at=self._now_datetime(),
                source_urls=["https://example.com/news/ev-1", "https://example.com/forum/battery"],
                summary="板块热度持续，头部与补涨梯队完整。",
                conclusion="发酵中",
                confidence=0.78,
                breakout_date=self._days_ago(17),
                trend_bull_type="A_B 慢牛加速",
                theme_name="固态电池",
                rise_reasons=[
                    "近20日量能斜率为正，资金持续流入",
                    "回调缩量且未破关键均线",
                    "题材热度维持在发酵区间",
                ],
            ),
            AIAnalysisRecord(
                provider="openai",
                symbol="sh600519",
                name="贵州茅台",
                fetched_at=(datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S"),
                source_urls=["https://example.com/news/consumption"],
                summary="消费主线维持，成交稳定。",
                conclusion="高潮",
                confidence=0.66,
                breakout_date=self._days_ago(26),
                trend_bull_type="A 阶梯慢牛",
                theme_name="高端消费",
                rise_reasons=[
                    "龙头股资金抱团明显",
                    "高位换手可控，回撤幅度有限",
                    "行业基本面预期稳定",
                ],
            ),
        ]

    def _enabled_ai_source_urls(self, limit: int = 5) -> list[str]:
        urls = [item.url for item in self._config.ai_sources if item.enabled and item.url.strip()]
        return urls[:limit]

    def _source_domains(self, source_urls: list[str]) -> set[str]:
        """Extract source domains from URLs using URLUtils."""
        return URLUtils.source_domains(source_urls)

    @staticmethod
    def _url_in_domains(url: str, domains: set[str]) -> bool:
        """Check if URL is in allowed domains using URLUtils."""
        return URLUtils.url_in_domains(url, domains)

    @staticmethod
    def _is_low_quality_source(source_name: str, source_url: str) -> bool:
        """Check if source is low quality using TextProcessor."""
        return TextProcessor.is_low_quality_source(source_name, source_url)

    @staticmethod
    def _is_low_signal_title(title: str) -> bool:
        """Check if title has low signal value using TextProcessor."""
        return TextProcessor.is_low_signal_title(title)

    @staticmethod
    def _clean_event_text(text: str) -> str:
        """Clean event text with media filtering."""
        cleaned = TextProcessor.clean_whitespace(text)
        cleaned = re.sub(r"^\[[^\]]+\]\s*", "", cleaned)
        cleaned = re.sub(r"^(新闻线索|信息源摘要|来源)[:：]\s*", "", cleaned)
        cleaned = re.sub(r"https?://\S+", "", cleaned)
        media = (
            "东方财富|财联社|巨潮资讯|同花顺|新浪财经|新浪网|证券时报|每日经济新闻|雪球|股吧|"
            "凤凰网|凤凰财经|SOHU|sohu|腾讯网|网易财经|和讯网|金融界"
        )
        cleaned = re.sub(rf"({media})\s*[|｜]", "", cleaned)
        cleaned = re.sub(rf"[（(]({media})[^)）]*[)）]", "", cleaned)
        cleaned = TextProcessor.clean_whitespace(cleaned)
        cleaned = re.sub(rf"\s*[-|｜]\s*({media})\s*$", "", cleaned)
        cleaned = re.sub(rf"\s*({media})\s*$", "", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" -|｜")
        return cleaned[:96]

    @staticmethod
    def _clean_text(text: str) -> str:
        """Simple text cleaning without media filtering."""
        return TextProcessor.clean_whitespace(TextProcessor.strip_html_tags(text))

    @staticmethod
    def _normalize_rise_reasons(reasons: list[str]) -> list[str]:
        """Normalize and deduplicate rise reasons with keyword prioritization."""
        cleaned: list[str] = []
        seen: set[str] = set()
        high_signal_keywords = (
            "收购",
            "并购",
            "重组",
            "增资",
            "注入",
            "标的",
            "订单",
            "中标",
            "合同",
            "签约",
            "业绩",
            "预增",
            "利润",
            "扭亏",
            "政策",
            "涨价",
            "新品",
            "合作",
            "回购",
            "激励",
            "投产",
            "扩产",
            "停牌",
            "核查",
            "问询",
            "监管",
            "警示",
            "入主",
            "景气",
            "需求",
            "供给",
            "回暖",
            "复苏",
            "资金",
        )
        for item in reasons:
            value = InMemoryStore._clean_event_text(item)
            if not value:
                continue
            lowered = value.lower()
            if value in seen:
                continue
            seen.add(value)
            cleaned.append(value)

        prioritized = [item for item in cleaned if any(keyword in item.lower() for keyword in high_signal_keywords)]
        if prioritized:
            return prioritized[:4]
        return cleaned[:4]

    @staticmethod
    def _extract_code_tokens(text: str) -> set[str]:
        """Extract stock code tokens from text."""
        return TextProcessor.extract_code_tokens(text)

    @staticmethod
    def _truncate_reason(text: str, max_len: int = 26) -> str:
        """Truncate text to max length, preserving word boundaries."""
        return TextProcessor.truncate_reason(text, max_len)

    @staticmethod
    def _extract_industry_label(industry_event_candidates: list[str] | None) -> str:
        for item in industry_event_candidates or []:
            text = InMemoryStore._clean_event_text(item)
            match = re.search(r"行业驱动[:：]?\s*([^\s，。；;]{2,12}?)(?:板块|行业)", text)
            if match:
                return match.group(1).strip()
            match = re.search(r"([^\s，。；;]{2,12}?)(?:板块|行业)", text)
            if match:
                return match.group(1).strip()
        return ""

    def _compact_reason_by_keywords(
        self,
        text: str,
        *,
        industry_mode: bool = False,
        industry_label: str = "",
    ) -> str | None:
        raw = self._clean_event_text(text)
        if not raw:
            return None

        # Remove long quoted headline fragments and trailing source-like tails.
        raw = re.sub(r"[“\"].{8,48}?[”\"]", "", raw)
        raw = re.sub(r"\s*[-|｜:：]\s*(财联社|东方财富|同花顺|新浪|证券时报|每日经济新闻).*$", "", raw)
        raw = TextProcessor.clean_whitespace(raw).strip("。；;，, ")
        if not raw:
            return None

        bucket = industry_label or "相关行业"

        if any(word in raw for word in ("收购", "并购", "重组", "拟购", "资产注入", "入主")):
            return self._truncate_reason(
                f"{'行业驱动：' if industry_mode else ''}{bucket if industry_mode else ''}并购重组预期升温"
            )
        if any(word in raw for word in ("订单", "中标", "合同", "签约")):
            return self._truncate_reason(
                f"{'行业驱动：' if industry_mode else ''}{bucket if industry_mode else ''}订单与项目预期改善"
            )
        if any(word in raw for word in ("业绩", "预增", "利润", "扭亏")):
            return self._truncate_reason(
                f"{'行业驱动：' if industry_mode else ''}{bucket if industry_mode else ''}业绩改善预期强化"
            )
        if any(word in raw for word in ("政策", "补贴", "规划", "支持", "国补", "降准", "降息")):
            return self._truncate_reason(
                f"{'行业驱动：' if industry_mode else ''}{bucket if industry_mode else ''}政策催化提升景气"
            )
        if any(word in raw for word in ("涨价", "提价")):
            return self._truncate_reason(
                f"{'行业驱动：' if industry_mode else ''}{bucket if industry_mode else ''}价格上行带动预期"
            )
        if any(word in raw for word in ("出海", "海外", "出口")):
            return self._truncate_reason(
                f"{'行业驱动：' if industry_mode else ''}{bucket if industry_mode else ''}海外需求扩张"
            )
        if any(word in raw for word in ("算力", "AI", "人工智能")):
            return self._truncate_reason(
                f"{'行业驱动：' if industry_mode else ''}{bucket if industry_mode else ''}AI需求拉动景气"
            )
        if any(word in raw for word in ("扩产", "投产", "产能")):
            return self._truncate_reason(
                f"{'行业驱动：' if industry_mode else ''}{bucket if industry_mode else ''}产能扩张预期增强"
            )
        if any(word in raw for word in ("景气", "需求", "回暖", "复苏", "供给")):
            return self._truncate_reason(
                f"{'行业驱动：' if industry_mode else ''}{bucket if industry_mode else ''}景气与需求回暖"
            )

        if industry_mode:
            return self._truncate_reason(f"行业驱动：{bucket}板块资金共振")
        if len(raw) < 8:
            return None
        return self._truncate_reason(raw)

    def _sanitize_ai_rise_reasons(
        self,
        reasons: list[str],
        *,
        symbol: str,
        core_event_candidates: list[str] | None = None,
        industry_event_candidates: list[str] | None = None,
        industry_hint: str = "",
    ) -> list[str]:
        symbol_code = symbol.lower().replace("sh", "").replace("sz", "").replace("bj", "")
        industry_label = self._extract_industry_label(industry_event_candidates) or TextProcessor.clean_whitespace(industry_hint)
        combined: list[str] = []
        if core_event_candidates:
            combined.extend(core_event_candidates)
        if industry_event_candidates:
            combined.extend(industry_event_candidates)
        combined.extend(reasons)

        filtered: list[str] = []
        event_keywords = (
            "收购",
            "并购",
            "重组",
            "拟购",
            "增资",
            "注入",
            "标的",
            "订单",
            "中标",
            "合同",
            "签约",
            "业绩",
            "预增",
            "利润",
            "扭亏",
            "政策",
            "涨价",
            "新品",
            "合作",
            "回购",
            "激励",
            "投产",
            "扩产",
            "停牌",
            "核查",
            "问询",
            "监管",
            "警示",
            "入主",
            "景气",
            "需求",
            "供给",
            "回暖",
            "复苏",
            "资金",
        )
        banned_phrases = (
            "强势涨停",
            "果断上车",
            "上车",
            "主升浪",
            "牛股",
            "龙头",
            "看多",
            "建议关注",
            "跟随",
            "股价连涨",
            "恐难支撑",
            "压力位",
            "支撑位",
            "摇摇欲坠",
            "引爆股价",
        )

        if (not core_event_candidates) and industry_event_candidates:
            industry_normalized = self._normalize_rise_reasons(industry_event_candidates)
            industry_event_only = [item for item in industry_normalized if any(keyword in item for keyword in event_keywords)]
            if industry_event_only:
                tagged: list[str] = []
                for item in industry_event_only[:2]:
                    compact = self._compact_reason_by_keywords(
                        item,
                        industry_mode=True,
                        industry_label=industry_label,
                    )
                    if compact:
                        tagged.append(compact)
                if tagged:
                    return list(dict.fromkeys(tagged))

        for item in combined:
            for piece in re.split(r"[；;。]\s*", str(item)):
                text = self._clean_event_text(piece)
                if not text:
                    continue
                if self._is_low_signal_title(text):
                    continue
                if any(phrase in text for phrase in banned_phrases):
                    continue
                codes = self._extract_code_tokens(text)
                if codes and any(code != symbol_code for code in codes):
                    continue
                if len(text) < 6:
                    continue
                if text.count("、") >= 2 and not any(k in text for k in ("收购", "并购", "重组", "订单", "中标", "业绩", "预增")):
                    continue
                is_industry_text = text.startswith("行业驱动：") or any(
                    self._clean_event_text(candidate) in text or text in self._clean_event_text(candidate)
                    for candidate in (industry_event_candidates or [])
                )
                compact = self._compact_reason_by_keywords(
                    text,
                    industry_mode=is_industry_text,
                    industry_label=industry_label,
                )
                if compact:
                    filtered.append(compact)

        normalized = self._normalize_rise_reasons(filtered)
        event_only = [item for item in normalized if any(keyword in item for keyword in event_keywords)]
        if event_only:
            concise = [
                self._compact_reason_by_keywords(
                    item,
                    industry_mode=item.startswith("行业驱动："),
                    industry_label=industry_label,
                )
                for item in event_only[:4]
            ]
            return [item for item in concise if item]

        core_normalized = self._normalize_rise_reasons(core_event_candidates or [])
        core_event_only = [item for item in core_normalized if any(keyword in item for keyword in event_keywords)]
        if core_event_only:
            concise = [
                self._compact_reason_by_keywords(item, industry_mode=False, industry_label=industry_label)
                for item in core_event_only[:4]
            ]
            return [item for item in concise if item]

        industry_normalized = self._normalize_rise_reasons(industry_event_candidates or [])
        industry_event_only = [item for item in industry_normalized if any(keyword in item for keyword in event_keywords)]
        if industry_event_only:
            tagged: list[str] = []
            for item in industry_event_only[:2]:
                compact = self._compact_reason_by_keywords(
                    item,
                    industry_mode=True,
                    industry_label=industry_label,
                )
                if compact:
                    tagged.append(compact)
            if tagged:
                return list(dict.fromkeys(tagged))
        return []

    def _sanitize_theme_name(
        self,
        theme_name: str,
        *,
        evidence: list[dict[str, str]],
        inferred_sector: str,
    ) -> str:
        value = TextProcessor.clean_whitespace(theme_name)
        generic = {"", "Unknown", "未知题材", "潜在热点", "主线热点"}
        if value in generic:
            inferred_theme = self._infer_theme_from_web_evidence(evidence)
            if inferred_theme != "Unknown":
                return inferred_theme
            return inferred_sector if inferred_sector != "Unknown" else "Unknown"

        corpus = " ".join(
            f"{item.get('title', '')} {item.get('snippet', '')}" for item in evidence
        )
        if value not in corpus and inferred_sector != "Unknown":
            inferred_theme = self._infer_theme_from_web_evidence(evidence)
            if inferred_theme != "Unknown":
                return inferred_theme
            return inferred_sector
        return value

    def _build_compact_ai_summary(
        self,
        *,
        symbol: str,
        breakout_date: str | None,
        board_label: str,
        inferred_sector: str,
        rise_reasons: list[str],
        raw_summary: str,
        row: ScreenerResult | None = None,
    ) -> str:
        lead = rise_reasons[0].rstrip("。；; ") if rise_reasons else ""
        summary = self._clean_event_text(raw_summary)
        extras: list[str] = []
        if summary and lead and summary != lead:
            first = re.split(r"[。；;]", summary)[0].strip()
            if first and first != lead and len(first) >= 8 and (lead not in first and first not in lead) and "核心催化" not in first:
                extras.append(first[:36].rstrip("。；; "))
        head = f"{lead}。" if lead.startswith("行业驱动：") else (f"核心催化：{lead}。" if lead else "")
        sector_text = inferred_sector if inferred_sector and inferred_sector != "Unknown" else "待确认"
        body = f"板块={board_label}，行业={sector_text}。"
        tail = f"补充：{extras[0]}。" if extras else ""
        quant = ""
        candles = self._ensure_candles(symbol)
        if candles:
            aligned_breakout = self._align_date_to_candles(candles, breakout_date or candles[-1].time)
            index_by_date = {item.time: idx for idx, item in enumerate(candles)}
            start_idx = index_by_date.get(aligned_breakout, len(candles) - 1)
            start_idx = max(0, min(start_idx, len(candles) - 1))
            start_close = max(0.01, candles[start_idx].close)
            latest_close = candles[-1].close
            high_slice = [item.high for item in candles[start_idx:]]
            peak_rel = max(range(len(high_slice)), key=lambda i: high_slice[i]) if high_slice else 0
            peak_idx = start_idx + peak_rel
            peak_high = max(0.01, candles[peak_idx].high)
            peak_date = candles[peak_idx].time

            rise_to_peak = (peak_high - start_close) / start_close
            current_vs_breakout = (latest_close - start_close) / start_close
            pullback_from_peak = max(0.0, (peak_high - latest_close) / peak_high)
            in_pullback = peak_idx < len(candles) - 1 and pullback_from_peak >= 0.015

            quant = (
                f"量化：起爆{aligned_breakout}→高点{peak_date}涨幅{rise_to_peak * 100:.2f}%，"
                f"当前较起爆{current_vs_breakout * 100:.2f}%，"
                f"{'高点回撤' + format(pullback_from_peak * 100, '.2f') + '%。' if in_pullback else '尚未明显回撤。'}"
            )
            latest_date = candles[-1].time
            latest_idx = len(candles) - 1
            idx_5 = max(0, latest_idx - 5)
            idx_10 = max(0, latest_idx - 10)
            base_close_5 = max(0.01, candles[idx_5].close)
            base_close_10 = max(0.01, candles[idx_10].close)
            ret_5 = (latest_close - base_close_5) / base_close_5
            ret_10 = (latest_close - base_close_10) / base_close_10
            recent_high_20 = max(point.high for point in candles[max(0, latest_idx - 19): latest_idx + 1])
            recent_low_20 = min(point.low for point in candles[max(0, latest_idx - 19): latest_idx + 1])
            drawdown_20 = max(0.0, (recent_high_20 - latest_close) / max(recent_high_20, 0.01))
            ma20_latest = self._safe_mean([point.close for point in candles[max(0, latest_idx - 19): latest_idx + 1]])
            above_ma20 = latest_close >= ma20_latest
            if ret_5 >= 0.03 and drawdown_20 <= 0.05:
                near_status = "短线强势延续"
            elif ret_5 >= 0 and above_ma20:
                near_status = "高位震荡偏强"
            elif above_ma20:
                near_status = "回踩整理"
            elif drawdown_20 >= 0.12:
                near_status = "短线转弱"
            else:
                near_status = "震荡观察"
            quant += (
                f"近况：截至{latest_date}，近5日{ret_5 * 100:+.2f}%，"
                f"近10日{ret_10 * 100:+.2f}%，20日高低区间[{recent_low_20:.2f},{recent_high_20:.2f}]，"
                f"当前状态={near_status}。"
            )
        if row is not None:
            amount20_yi = row.amount20 / 1e8 if row.amount20 > 0 else 0.0
            liquidity = (
                f"流动性：20日平均换手{row.turnover20 * 100:.2f}%"
                f"{f'，20日平均成交额{amount20_yi:.2f}亿' if amount20_yi > 0 else ''}。"
            )
            quant = f"{quant}{liquidity}" if quant else liquidity
        return f"{head}{body}{tail}{quant}"

    @staticmethod
    def _extract_core_event_candidates(evidence: list[dict[str, str]]) -> list[str]:
        event_keywords = (
            "收购",
            "并购",
            "重组",
            "拟购",
            "增资",
            "中标",
            "订单",
            "合同",
            "签约",
            "业绩",
            "预增",
            "扭亏",
            "涨价",
        )
        candidates: list[str] = []
        seen: set[str] = set()
        for item in evidence[:10]:
            text = InMemoryStore._clean_event_text(str(item.get("title", "")))
            if not text:
                continue
            if not any(keyword in text for keyword in event_keywords):
                continue
            if text in seen:
                continue
            seen.add(text)
            candidates.append(text[:96])
            if len(candidates) >= 4:
                break
        return candidates

    def _extract_industry_event_candidates(
        self,
        industry: str,
        evidence: list[dict[str, str]],
    ) -> list[str]:
        industry_name = TextProcessor.clean_whitespace(industry)
        if not industry_name:
            return []
        event_keywords = (
            "政策",
            "补贴",
            "景气",
            "需求",
            "供给",
            "涨价",
            "扩产",
            "产能",
            "订单",
            "库存",
            "周期",
            "国产替代",
            "降息",
            "降准",
        )
        banned = ("个股", "股价", "涨停", "跌停", "龙头", "复盘", "早盘", "午评", "收评")
        candidates: list[str] = []
        seen: set[str] = set()
        for item in evidence[:12]:
            text = self._clean_event_text(
                f"{item.get('title', '')} {item.get('snippet', '')}"
            )
            if not text:
                continue
            if industry_name not in text:
                continue
            if any(
                token in text
                for token in ("基金", "份额", "净值", "产品资料概要", "发起式", "证券投资基金", "公告更新", "招募说明书")
            ):
                continue
            if any(word in text for word in banned):
                continue
            if any(code != "" for code in self._extract_code_tokens(text)):
                continue
            if "、" in text and not any(word in text for word in ("政策", "需求", "供给", "涨价", "景气", "订单", "产能")):
                continue
            if not any(word in text for word in event_keywords):
                continue
            if text in seen:
                continue
            seen.add(text)
            candidates.append(text[:60])
            if len(candidates) >= 3:
                break
        return candidates

    def _build_industry_fallback_reasons(
        self,
        industry: str,
        row: ScreenerResult | None,
    ) -> list[str]:
        industry_name = TextProcessor.clean_whitespace(industry)
        if not industry_name or industry_name == "Unknown":
            return []
        reasons: list[str] = [f"行业驱动：{industry_name}板块近期放量走强，行业资金共振带动个股补涨。"]
        if row is not None:
            if row.up_down_volume_ratio >= 1.1:
                reasons.append(f"行业驱动：{industry_name}上涨日量能占优，短线资金风险偏好回升。")
            elif row.retrace20 <= 0.12:
                reasons.append(f"行业驱动：{industry_name}板块回撤受控，行业资金维持轮动配置。")
            else:
                reasons.append(f"行业驱动：{industry_name}景气预期回暖，行业资金从核心向二线扩散。")
        return reasons[:2]

    @staticmethod
    def _parse_rss_pub_date(date_text: str) -> datetime | None:
        if not date_text.strip():
            return None
        try:
            parsed = parsedate_to_datetime(date_text.strip())
            if parsed.tzinfo is not None:
                return parsed.astimezone().replace(tzinfo=None)
            return parsed
        except Exception:
            return None

    @staticmethod
    def _market_board_label(symbol: str) -> str:
        code = symbol.lower().strip()
        if code.startswith("sz300") or code.startswith("sz301"):
            return "创业板"
        if code.startswith("sh688"):
            return "科创板"
        if code.startswith("bj"):
            return "北交所"
        return "主板"

    @staticmethod
    def _symbol_to_secid(symbol: str) -> str | None:
        raw = symbol.lower().strip()
        market = ""
        code = ""
        if raw.startswith("sz"):
            market, code = "0", raw[2:]
        elif raw.startswith("sh"):
            market, code = "1", raw[2:]
        elif raw.startswith("bj"):
            market, code = "0", raw[2:]
        elif re.fullmatch(r"\d{6}", raw):
            code = raw
            market = "1" if raw.startswith(("5", "6", "9")) else "0"
        if not code or not re.fullmatch(r"\d{6}", code):
            return None
        return f"{market}.{code}"

    def _fetch_quote_profile(self, symbol: str) -> dict[str, str]:
        cache_key = symbol.lower()
        now_ts = time.time()
        cached = self._quote_profile_cache.get(cache_key)
        if cached and now_ts - cached[0] <= 3600:
            return cached[1]

        secid = self._symbol_to_secid(symbol)
        if secid is None:
            profile = {"name": "", "industry": "", "region": ""}
            self._quote_profile_cache[cache_key] = (now_ts, profile)
            return profile

        profile = {"name": "", "industry": "", "region": ""}
        try:
            with httpx.Client(timeout=6.0, follow_redirects=True) as client:
                resp = client.get(
                    "https://push2.eastmoney.com/api/qt/stock/get",
                    params={"secid": secid, "fields": "f57,f58,f127,f128"},
                    headers={
                        "User-Agent": (
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
                        )
                    },
                )
            resp.raise_for_status()
            payload = resp.json()
            data = payload.get("data", {}) if isinstance(payload, dict) else {}
            profile = {
                "name": str(data.get("f58", "")).strip(),
                "industry": str(data.get("f127", "")).strip(),
                "region": str(data.get("f128", "")).strip(),
            }
        except Exception:
            profile = {"name": "", "industry": "", "region": ""}

        self._quote_profile_cache[cache_key] = (now_ts, profile)
        return profile

    def _build_web_search_queries(
        self,
        symbol: str,
        stock_name: str,
        source_domains: set[str],
        focus_date: str | None = None,
    ) -> list[str]:
        base_queries = [
            f"{symbol} {stock_name} 收购 并购 重组",
            f"{symbol} {stock_name} 并购 标的 公告",
            f"{symbol} {stock_name} 上涨原因 题材 新闻",
            f"{symbol} {stock_name} 涨停 原因 公告",
            f"{symbol} {stock_name} 板块 热点",
            f"{symbol} {stock_name} 公告 业绩",
        ]
        parsed_focus = self._parse_date(focus_date or "")
        if parsed_focus is not None:
            month_key = parsed_focus.strftime("%Y-%m")
            base_queries.insert(0, f"{symbol} {stock_name} {month_key} 公告 热点")
            base_queries.insert(1, f"{symbol} {stock_name} {month_key} 板块 题材")
        queries: list[str] = []
        for domain in list(source_domains)[:4]:
            queries.append(f"{symbol} {stock_name} 上涨原因 site:{domain}")
            queries.append(f"{symbol} {stock_name} 题材 site:{domain}")
        queries.extend(base_queries)
        return queries

    def _build_industry_search_queries(
        self,
        industry: str,
        source_domains: set[str],
        focus_date: str | None = None,
    ) -> list[str]:
        base_queries = [
            f"{industry} 板块 大涨 原因",
            f"{industry} 行业 景气 政策",
            f"{industry} 需求 供给 价格",
            f"{industry} 产业链 催化",
        ]
        parsed_focus = self._parse_date(focus_date or "")
        if parsed_focus is not None:
            month_key = parsed_focus.strftime("%Y-%m")
            base_queries.insert(0, f"{industry} {month_key} 板块 大涨 原因")
            base_queries.insert(1, f"{industry} {month_key} 政策 催化")
        queries: list[str] = []
        for domain in list(source_domains)[:4]:
            queries.append(f"{industry} 大涨 原因 site:{domain}")
            queries.append(f"{industry} 行业 催化 site:{domain}")
        queries.extend(base_queries)
        return queries

    def _collect_web_evidence(
        self,
        symbol: str,
        stock_name: str,
        source_urls: list[str],
        *,
        focus_date: str | None = None,
        max_items: int = 8,
    ) -> list[dict[str, str]]:
        cache_key = f"{symbol}:{focus_date or ''}:{','.join(source_urls)}"
        now_ts = time.time()
        cached = self._web_evidence_cache.get(cache_key)
        if cached and now_ts - cached[0] <= 600:
            return cached[1]

        allowed_domains = self._source_domains(source_urls)
        queries = self._build_web_search_queries(symbol, stock_name, allowed_domains, focus_date)
        timeout = max(5.0, min(float(self._config.ai_timeout_sec), 12.0))
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            )
        }

        results: list[dict[str, str]] = []
        seen_urls: set[str] = set()

        def parse_rss(xml_text: str, domain_filter: set[str]) -> list[dict[str, str]]:
            parsed_items: list[dict[str, str]] = []
            try:
                root = ET.fromstring(xml_text)
            except ET.ParseError:
                return parsed_items
            for item in root.findall("./channel/item"):
                link = (item.findtext("link") or "").strip()
                source_node = item.find("source")
                source_name = ""
                source_url = ""
                if source_node is not None:
                    source_name = (source_node.text or "").strip()
                    source_url = (source_node.attrib.get("url") or "").strip()
                if self._is_low_quality_source(source_name, source_url):
                    continue
                filter_url = source_url or link
                display_url = source_url or link
                if display_url in seen_urls and link and link not in seen_urls:
                    display_url = link
                dedupe_key = display_url
                if not display_url or dedupe_key in seen_urls:
                    continue
                if not self._url_in_domains(filter_url, domain_filter):
                    continue
                title_raw = html.unescape((item.findtext("title") or "").strip())
                desc_raw = html.unescape((item.findtext("description") or "").strip())
                title = self._clean_text(title_raw)
                snippet = self._clean_text(desc_raw)
                if self._is_low_signal_title(title):
                    continue
                if stock_name and stock_name not in f"{title} {snippet}" and symbol.lower() not in f"{title} {snippet}".lower():
                    continue
                if not title and not snippet:
                    continue
                parsed_items.append(
                    {
                        "title": title[:120] if title else "无标题",
                        "url": display_url,
                        "snippet": (f"{source_name} | {snippet}" if source_name else snippet)[:260] if snippet else "无摘要",
                        "pub_date": (item.findtext("pubDate") or "").strip()[:40],
                        "source_name": source_name[:40],
                    }
                )
                seen_urls.add(dedupe_key)
                if len(results) + len(parsed_items) >= max_items:
                    break
            return parsed_items

        try:
            with httpx.Client(timeout=timeout, follow_redirects=True, headers=headers) as client:
                for query in queries:
                    if len(results) >= max_items:
                        break
                    response = client.get(
                        "https://news.google.com/rss/search",
                        params={
                            "q": query,
                            "hl": "zh-CN",
                            "gl": "CN",
                            "ceid": "CN:zh-Hans",
                        },
                    )
                    response.raise_for_status()

                    parsed_items = parse_rss(response.text, allowed_domains)
                    if not parsed_items and allowed_domains:
                        parsed_items = parse_rss(response.text, set())
                    results.extend(parsed_items)
                    if len(results) >= max_items:
                        break
        except Exception:
            results = []

        results = results[:max_items]
        self._web_evidence_cache[cache_key] = (now_ts, results)
        return results

    def _collect_industry_evidence(
        self,
        industry: str,
        source_urls: list[str],
        *,
        focus_date: str | None = None,
        max_items: int = 8,
    ) -> list[dict[str, str]]:
        industry_name = TextProcessor.clean_whitespace(industry)
        if not industry_name or industry_name == "Unknown":
            return []
        cache_key = f"industry:{industry_name}:{focus_date or ''}:{','.join(source_urls)}"
        now_ts = time.time()
        cached = self._web_evidence_cache.get(cache_key)
        if cached and now_ts - cached[0] <= 600:
            return cached[1]

        allowed_domains = self._source_domains(source_urls)
        queries = self._build_industry_search_queries(industry_name, allowed_domains, focus_date)
        timeout = max(5.0, min(float(self._config.ai_timeout_sec), 12.0))
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            )
        }

        results: list[dict[str, str]] = []
        seen_urls: set[str] = set()

        def parse_rss(xml_text: str, domain_filter: set[str]) -> list[dict[str, str]]:
            parsed_items: list[dict[str, str]] = []
            try:
                root = ET.fromstring(xml_text)
            except ET.ParseError:
                return parsed_items
            for item in root.findall("./channel/item"):
                link = (item.findtext("link") or "").strip()
                source_node = item.find("source")
                source_name = ""
                source_url = ""
                if source_node is not None:
                    source_name = (source_node.text or "").strip()
                    source_url = (source_node.attrib.get("url") or "").strip()
                if self._is_low_quality_source(source_name, source_url):
                    continue
                filter_url = source_url or link
                display_url = source_url or link
                if display_url in seen_urls and link and link not in seen_urls:
                    display_url = link
                if not display_url or display_url in seen_urls:
                    continue
                if not self._url_in_domains(filter_url, domain_filter):
                    continue
                title_raw = html.unescape((item.findtext("title") or "").strip())
                desc_raw = html.unescape((item.findtext("description") or "").strip())
                title = self._clean_text(title_raw)
                snippet = self._clean_text(desc_raw)
                if self._is_low_signal_title(title):
                    continue
                if industry_name not in f"{title} {snippet}":
                    continue
                if not title and not snippet:
                    continue
                parsed_items.append(
                    {
                        "title": title[:120] if title else "无标题",
                        "url": display_url,
                        "snippet": (f"{source_name} | {snippet}" if source_name else snippet)[:260] if snippet else "无摘要",
                        "pub_date": (item.findtext("pubDate") or "").strip()[:40],
                        "source_name": source_name[:40],
                    }
                )
                seen_urls.add(display_url)
                if len(results) + len(parsed_items) >= max_items:
                    break
            return parsed_items

        try:
            with httpx.Client(timeout=timeout, follow_redirects=True, headers=headers) as client:
                for query in queries:
                    if len(results) >= max_items:
                        break
                    response = client.get(
                        "https://news.google.com/rss/search",
                        params={
                            "q": query,
                            "hl": "zh-CN",
                            "gl": "CN",
                            "ceid": "CN:zh-Hans",
                        },
                    )
                    response.raise_for_status()

                    parsed_items = parse_rss(response.text, allowed_domains)
                    if not parsed_items and allowed_domains:
                        parsed_items = parse_rss(response.text, set())
                    results.extend(parsed_items)
                    if len(results) >= max_items:
                        break
        except Exception:
            results = []

        results = results[:max_items]
        self._web_evidence_cache[cache_key] = (now_ts, results)
        return results

    def _active_ai_provider(self) -> AIProviderConfig | None:
        for provider in self._config.ai_providers:
            if provider.id == self._config.ai_provider and provider.enabled:
                return provider
        return None

    @staticmethod
    def _read_api_key_file(path_text: str) -> str:
        if not path_text.strip():
            return ""
        full = os.path.expandvars(path_text.strip())
        full = os.path.expanduser(full)
        try:
            with open(full, "r", encoding="utf-8") as fp:
                return fp.read().strip()
        except OSError:
            return ""

    def _resolve_provider_api_key(
        self,
        provider: AIProviderConfig | None,
        *,
        fallback_api_key: str = "",
        fallback_api_key_path: str = "",
    ) -> str:
        if provider and provider.api_key.strip():
            if provider.api_key != '********':
                return provider.api_key.strip()
            saved = next((item for item in self._config.ai_providers if item.id == provider.id), None)
            if saved and saved.api_key:
                return saved.api_key.strip()
        if self._config.api_key.strip():
            return self._config.api_key.strip()
        if fallback_api_key.strip():
            return fallback_api_key.strip()
        if provider and provider.api_key_path.strip():
            key = self._read_api_key_file(provider.api_key_path)
            if key:
                return key
        if self._config.api_key_path.strip():
            key = self._read_api_key_file(self._config.api_key_path)
            if key:
                return key
        if fallback_api_key_path.strip():
            key = self._read_api_key_file(fallback_api_key_path)
            if key:
                return key
        return ""

    @staticmethod
    def _trend_bull_type_label(trend: TrendClass) -> str:
        if trend == "A":
            return "A 阶梯慢牛"
        if trend == "A_B":
            return "A_B 慢牛加速"
        if trend == "B":
            return "B 脉冲涨停牛"
        return "Unknown"

    def _resolve_symbol_name(self, symbol: str, row: ScreenerResult | None = None) -> str:
        def _valid_name(name: str | None) -> str | None:
            if not name:
                return None
            value = name.strip()
            if not value:
                return None
            if value.upper() == symbol.upper():
                return None
            return value

        if row:
            resolved = _valid_name(row.name)
            if resolved:
                return resolved

        cached = self._latest_rows.get(symbol)
        if cached:
            resolved = _valid_name(cached.name)
            if resolved:
                return resolved

        for run in reversed(list(self._run_store.values())):
            for pool in (
                run.step_pools.step4,
                run.step_pools.step3,
                run.step_pools.step2,
                run.step_pools.step1,
                run.step_pools.input,
            ):
                found = next((item for item in pool if item.symbol == symbol), None)
                if not found:
                    continue
                resolved = _valid_name(found.name)
                if resolved:
                    return resolved

        profile = self._fetch_quote_profile(symbol)
        resolved = _valid_name(profile.get("name"))
        if resolved:
            return resolved

        base = next((item for item in STOCK_POOL if item["symbol"] == symbol), None)
        if base and base.get("name"):
            return str(base["name"])
        return symbol.upper()

    @staticmethod
    def _safe_mean(values: list[float] | list[int]) -> float:
        if not values:
            return 0.0
        return float(sum(values) / len(values))

    @staticmethod
    def _parse_date(date_text: str) -> datetime | None:
        try:
            return datetime.strptime(date_text, "%Y-%m-%d")
        except ValueError:
            return None

    def _align_date_to_candles(self, candles: list[CandlePoint], date_text: str) -> str:
        if not candles:
            return date_text
        parsed_target = self._parse_date(date_text)
        if parsed_target is None:
            return candles[-1].time
        for candle in reversed(candles):
            parsed_candle = self._parse_date(candle.time)
            if parsed_candle and parsed_candle <= parsed_target:
                return candle.time
        return candles[0].time

    def _slice_candles_as_of(
        self,
        candles: list[CandlePoint],
        as_of_date: str | None,
    ) -> tuple[list[CandlePoint], str | None]:
        if not candles:
            return [], None
        if not as_of_date:
            return candles, candles[-1].time
        aligned = self._align_date_to_candles(candles, as_of_date)
        for idx, point in enumerate(candles):
            if point.time == aligned:
                return candles[: idx + 1], aligned
        return candles, candles[-1].time

    @staticmethod
    def _trading_days_diff(
        candles: list[CandlePoint],
        *,
        start_date: str,
        end_date: str,
    ) -> int:
        if not candles:
            return 0
        trade_days = [str(point.time) for point in candles if str(point.time).strip()]
        if not trade_days:
            return 0
        end_idx = bisect_right(trade_days, str(end_date)) - 1
        if end_idx < 0:
            return 0
        start_idx = bisect_right(trade_days, str(start_date)) - 1
        if start_idx < 0:
            start_idx = 0
        if start_idx > end_idx:
            return 0
        return int(end_idx - start_idx)

    def _compute_signal_age_days(
        self,
        *,
        symbol: str,
        trigger_date: str,
        as_of_date: str | None,
    ) -> tuple[int, str | None]:
        candles, resolved_as_of = self._slice_candles_as_of(
            self._ensure_candles(symbol),
            as_of_date,
        )
        if not candles:
            return 0, resolved_as_of or as_of_date
        end_date = str(resolved_as_of or candles[-1].time)
        age_days = self._trading_days_diff(
            candles,
            start_date=str(trigger_date),
            end_date=end_date,
        )
        return max(0, int(age_days)), end_date

    def _infer_recent_rebreakout_index(self, candles: list[CandlePoint]) -> int | None:
        if len(candles) < 70:
            return None
        closes = [point.close for point in candles]
        highs = [point.high for point in candles]
        lows = [point.low for point in candles]
        volumes = [max(0, int(point.volume)) for point in candles]

        # Focus on the recent 45 bars to avoid returning an old rally ignition.
        start = max(45, len(candles) - 45)
        end = len(candles) - 2
        for idx in range(start, end + 1):
            prior_window_start = idx - 20
            if prior_window_start < 20:
                continue
            prior_high20 = max(highs[prior_window_start:idx])
            prior_low20 = min(lows[prior_window_start:idx])
            if prior_low20 <= 0:
                continue

            breakout = closes[idx] >= prior_high20 * 1.01
            avg_vol20 = self._safe_mean(volumes[prior_window_start:idx])
            volume_confirmed = volumes[idx] >= avg_vol20 * 1.15 if avg_vol20 > 0 else False

            consolidation_range = (prior_high20 - prior_low20) / max(prior_low20, 0.01)
            ma20_curr = self._safe_mean(closes[idx - 19 : idx + 1])
            ma20_prev = self._safe_mean(closes[idx - 29 : idx - 9])
            ma20_flat = abs(ma20_curr - ma20_prev) / max(ma20_prev, 0.01) <= 0.05

            pre_peak_start = max(0, idx - 60)
            pre_peak_end = max(pre_peak_start + 1, idx - 20)
            pre_peak = max(highs[pre_peak_start:pre_peak_end])
            prior_run_existed = pre_peak >= prior_high20 * 1.08

            ignition = (
                closes[idx] >= ma20_curr * 1.005
                and closes[idx] >= closes[idx - 1] * 1.015
                and volume_confirmed
            )
            if not (ma20_flat and consolidation_range <= 0.24 and prior_run_existed and (breakout or ignition)):
                continue

            forward_end = min(len(candles), idx + 11)
            if forward_end <= idx + 1:
                continue
            forward_high = max(highs[idx + 1 : forward_end])
            forward_ret = (forward_high - closes[idx]) / max(closes[idx], 0.01)
            if forward_ret >= 0.05:
                return idx
        return None

    def _collect_volume_price_breakout_candidates(
        self,
        candles: list[CandlePoint],
        *,
        lookback: int = 55,
        max_items: int = 4,
    ) -> list[tuple[int, float, float, bool, bool]]:
        if len(candles) < 35:
            return []

        closes = [point.close for point in candles]
        highs = [point.high for point in candles]
        lows = [point.low for point in candles]
        volumes = [max(0, int(point.volume)) for point in candles]
        start = max(20, len(candles) - lookback)
        end = len(candles) - 2
        if end <= start:
            return []

        scored: list[tuple[float, int, float, float, bool, bool, int]] = []
        for idx in range(start, end + 1):
            prev_close = closes[idx - 1]
            if prev_close <= 0:
                continue
            day_ret = (closes[idx] - prev_close) / prev_close
            avg_vol10 = self._safe_mean(volumes[max(0, idx - 10) : idx])
            if avg_vol10 <= 0:
                continue
            vol_ratio10 = volumes[idx] / avg_vol10
            prior_high20 = max(highs[idx - 20 : idx])
            is_breakout = closes[idx] >= prior_high20 * 1.005

            pre_start = max(0, idx - 15)
            pre_low = min(lows[pre_start:idx]) if idx > pre_start else lows[idx]
            pre_high = max(highs[pre_start:idx]) if idx > pre_start else highs[idx]
            pre_range = (pre_high - pre_low) / max(pre_low, 0.01)
            is_consolidation_end = pre_range <= 0.26 and closes[idx] >= pre_high * 0.995
            ma10_curr = self._safe_mean(closes[max(0, idx - 9) : idx + 1])
            ma20_curr = self._safe_mean(closes[max(0, idx - 19) : idx + 1])
            ma20_prev = self._safe_mean(closes[max(0, idx - 20) : idx]) if idx >= 20 else ma20_curr
            is_ma_ignition = (
                closes[idx] >= ma10_curr * 1.005
                and closes[idx] >= ma20_curr * 1.02
                and ma20_curr >= ma20_prev * 0.997
            )
            is_washout_reversal = False
            anchor_idx = idx
            if idx >= 6:
                prev5_high = max(closes[idx - 5 : idx])
                prev_drop_ratio = (closes[idx - 1] - prev5_high) / max(prev5_high, 0.01)
                intraday_rebound = (closes[idx] - lows[idx]) / max(lows[idx], 0.01)
                is_washout_reversal = (
                    day_ret >= 0.08
                    and vol_ratio10 >= 1.45
                    and prev_drop_ratio <= -0.10
                    and intraday_rebound >= 0.08
                )
                if is_washout_reversal:
                    low_start = max(0, idx - 4)
                    low_end = idx
                    if low_end > low_start:
                        anchor_idx = min(range(low_start, low_end), key=lambda i: closes[i])

            forward_end = min(len(candles), idx + 8)
            if forward_end <= idx + 1:
                continue
            forward_high = max(highs[idx + 1 : forward_end])
            forward_ret = (forward_high - closes[idx]) / max(closes[idx], 0.01)

            if day_ret < 0.04:
                continue
            if vol_ratio10 < 1.45:
                continue
            if not (is_breakout or is_consolidation_end or is_ma_ignition or is_washout_reversal):
                continue
            if forward_ret < 0.04:
                continue

            recency = (idx - start) / max(1, (end - start))
            score = (
                day_ret * 100
                + vol_ratio10 * 5.0
                + (3.0 if is_breakout else 0.0)
                + (2.0 if is_consolidation_end else 0.0)
                + (1.5 if is_ma_ignition else 0.0)
                + (2.5 if is_washout_reversal else 0.0)
                + forward_ret * 30
                + recency * 4.0
            )
            scored.append((score, anchor_idx, day_ret, vol_ratio10, is_breakout, is_washout_reversal, idx))

        scored.sort(key=lambda item: item[0], reverse=True)
        selected: list[tuple[int, float, float, bool, bool]] = []
        for _, idx, day_ret, vol_ratio10, is_breakout, is_washout_reversal, _ in scored:
            if any(abs(idx - picked_idx) <= 2 for picked_idx, _, _, _, _ in selected):
                continue
            selected.append((idx, day_ret, vol_ratio10, is_breakout, is_washout_reversal))
            if len(selected) >= max_items:
                break
        return selected

    def _build_recent_price_volume_snapshot(
        self,
        candles: list[CandlePoint],
        *,
        lookback: int = 16,
    ) -> str:
        if len(candles) < 2:
            return "insufficient_recent_kline"
        closes = [point.close for point in candles]
        highs = [point.high for point in candles]
        volumes = [max(0, int(point.volume)) for point in candles]
        start = max(1, len(candles) - lookback)
        lines: list[str] = []
        for idx in range(start, len(candles)):
            prev_close = closes[idx - 1]
            day_ret = (closes[idx] - prev_close) / max(prev_close, 0.01)
            avg_vol10 = self._safe_mean(volumes[max(0, idx - 10) : idx])
            vol_ratio10 = volumes[idx] / max(avg_vol10, 1.0)
            prior_high20 = max(highs[max(0, idx - 20) : idx]) if idx > 0 else highs[idx]
            break20 = closes[idx] >= prior_high20 * 1.005 if idx >= 20 else False
            lines.append(
                (
                    f"{candles[idx].time}"
                    f"|close={closes[idx]:.2f}"
                    f"|pct={day_ret * 100:.2f}%"
                    f"|vol10x={vol_ratio10:.2f}"
                    f"|break20={1 if break20 else 0}"
                )
            )
        return "\n".join(lines)

    def _adjust_to_cluster_lead_index(self, candles: list[CandlePoint], index: int) -> int:
        if index <= 0 or len(candles) < 25:
            return index
        closes = [point.close for point in candles]
        highs = [point.high for point in candles]
        volumes = [max(0, int(point.volume)) for point in candles]
        start = max(1, index - 3)
        lead = index
        for idx in range(index, start - 1, -1):
            prev_close = closes[idx - 1]
            day_ret = (closes[idx] - prev_close) / max(prev_close, 0.01)
            avg_vol10 = self._safe_mean(volumes[max(0, idx - 10) : idx])
            vol_ratio10 = volumes[idx] / max(avg_vol10, 1.0)
            prior_high20 = max(highs[max(0, idx - 20) : idx]) if idx > 0 else highs[idx]
            breakout_like = closes[idx] >= prior_high20 * 0.995
            ma10_curr = self._safe_mean(closes[max(0, idx - 9) : idx + 1])
            ma20_curr = self._safe_mean(closes[max(0, idx - 19) : idx + 1])
            ma20_prev = self._safe_mean(closes[max(0, idx - 20) : idx]) if idx >= 20 else ma20_curr
            ma_ignition = (
                closes[idx] >= ma10_curr * 1.005
                and closes[idx] >= ma20_curr * 1.02
                and ma20_curr >= ma20_prev * 0.997
            )
            if day_ret >= 0.025 and vol_ratio10 >= 1.3 and (breakout_like or ma_ignition):
                lead = idx
        return lead

    def _infer_breakout_index_from_candles(self, candles: list[CandlePoint]) -> int | None:
        if len(candles) < 40:
            return None

        # First priority: recent volume-price ignition (放量+长阳+突破/结束盘整).
        volume_price_candidates = self._collect_volume_price_breakout_candidates(candles, lookback=55, max_items=3)
        if volume_price_candidates:
            candidate_idx, _, _, _, is_washout_reversal = volume_price_candidates[0]
            if is_washout_reversal:
                return candidate_idx
            return self._adjust_to_cluster_lead_index(candles, candidate_idx)

        # Prefer "二次启动" when a clear consolidation followed by a fresh breakout exists.
        recent_rebreakout = self._infer_recent_rebreakout_index(candles)
        if recent_rebreakout is not None:
            return recent_rebreakout

        closes = [point.close for point in candles]
        highs = [point.high for point in candles]
        volumes = [max(0, int(point.volume)) for point in candles]
        start = max(20, len(candles) - 110)
        end = len(candles) - 8
        if end <= start:
            return None

        # Primary rule: first confirmed breakout + trend alignment + volume expansion.
        for idx in range(start, end):
            prior_high20 = max(highs[idx - 20 : idx])
            if prior_high20 <= 0:
                continue
            ma20 = self._safe_mean(closes[idx - 19 : idx + 1])
            ma10 = self._safe_mean(closes[idx - 9 : idx + 1])
            ma5 = self._safe_mean(closes[idx - 4 : idx + 1])
            avg_vol20 = self._safe_mean(volumes[idx - 20 : idx])
            if avg_vol20 <= 0:
                continue

            breakout = closes[idx] >= prior_high20 * 1.01
            trend_aligned = ma5 > ma10 > ma20 and closes[idx] >= ma20 * 1.005
            volume_confirmed = volumes[idx] >= avg_vol20 * 1.2
            forward_high = max(highs[idx + 1 : idx + 9])
            forward_ret = (forward_high - closes[idx]) / max(closes[idx], 0.01)
            if breakout and trend_aligned and volume_confirmed and forward_ret >= 0.10:
                return idx

        # Secondary rule: MA20上穿并在短期内有延续。
        for idx in range(start + 1, end):
            ma20_prev = self._safe_mean(closes[idx - 20 : idx])
            ma20_curr = self._safe_mean(closes[idx - 19 : idx + 1])
            if closes[idx - 1] <= ma20_prev and closes[idx] > ma20_curr and ma20_curr >= ma20_prev * 0.997:
                forward_high = max(highs[idx + 1 : idx + 9])
                forward_ret = (forward_high - closes[idx]) / max(closes[idx], 0.01)
                if forward_ret >= 0.08:
                    return idx

        # Final fallback: lowest point in recent trend window before acceleration.
        recent_start = max(0, len(candles) - 45)
        recent_end = max(recent_start + 1, len(candles) - 5)
        if recent_end > recent_start:
            return min(range(recent_start, recent_end), key=lambda i: closes[i])
        return None

    def _sanitize_breakout_date(self, symbol: str, raw_date: str, baseline_date: str) -> str:
        candles = self._ensure_candles(symbol)
        if not candles:
            return baseline_date
        baseline_aligned = self._align_date_to_candles(candles, baseline_date)
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw_date):
            return baseline_aligned

        raw_aligned = self._align_date_to_candles(candles, raw_date)
        date_to_index = {point.time: idx for idx, point in enumerate(candles)}
        raw_index = date_to_index.get(raw_aligned)
        baseline_index = date_to_index.get(baseline_aligned)
        if raw_index is None:
            return baseline_aligned
        if baseline_index is None:
            return raw_aligned

        # Clamp AI hallucination: breakout day should not drift too far from K-line baseline.
        if abs(raw_index - baseline_index) > 18:
            return baseline_aligned
        # Too close to current end usually means "latest acceleration", not "起爆".
        if raw_index >= len(candles) - 3:
            return baseline_aligned
        return raw_aligned

    def _build_row_from_candles(self, symbol: str, as_of_date: str | None = None) -> ScreenerResult | None:
        candles, _ = self._slice_candles_as_of(self._ensure_candles(symbol), as_of_date)
        if len(candles) < 30:
            return None

        closes = [item.close for item in candles]
        highs = [item.high for item in candles]
        lows = [item.low for item in candles]
        opens = [item.open for item in candles]
        volumes = [max(0, int(item.volume)) for item in candles]
        amounts = [max(0.0, float(item.amount)) for item in candles]

        latest = closes[-1]
        prev = closes[-2]
        look20 = min(20, len(closes))
        look40 = min(40, len(closes) - 1)
        if look40 < 5:
            return None

        start20 = len(closes) - look20
        start40 = len(closes) - look40
        start40_close = closes[start40]
        ret40 = (latest - start40_close) / max(start40_close, 0.01)

        high20 = max(highs[start20:])
        retrace20 = max(0.0, (high20 - latest) / max(high20, 0.01))
        amount20 = self._safe_mean(amounts[start20:])
        amplitude20 = self._safe_mean(
            [
                (highs[idx] - lows[idx]) / max(closes[idx], 0.01)
                for idx in range(start20, len(closes))
            ]
        )

        avg_vol10 = self._safe_mean(volumes[-10:])
        avg_vol_prev10 = self._safe_mean(volumes[-20:-10]) if len(volumes) >= 20 else avg_vol10
        vol_slope20 = (avg_vol10 - avg_vol_prev10) / max(avg_vol_prev10, 1.0)

        up_volumes: list[int] = []
        down_volumes: list[int] = []
        for idx in range(1, len(closes)):
            if closes[idx] >= closes[idx - 1]:
                up_volumes.append(volumes[idx])
            else:
                down_volumes.append(volumes[idx])
        up_down_volume_ratio = self._safe_mean(up_volumes) / max(self._safe_mean(down_volumes), 1.0)

        pullback_days = 0
        pb_window = min(20, len(closes))
        if pb_window >= 2:
            pb_slice = closes[-pb_window:]
            pb_peak_offset = max(range(len(pb_slice)), key=lambda i: pb_slice[i])
            pullback_days = len(pb_slice) - 1 - pb_peak_offset
        recent_pullback_volume = volumes[-pullback_days:] if pullback_days > 0 else []
        pullback_volume_ratio = (
            self._safe_mean(recent_pullback_volume) / max(self._safe_mean(volumes[-5:]), 1.0)
            if recent_pullback_volume
            else 0.85
        )

        ma20 = self._safe_mean(closes[-20:])
        ma10 = self._safe_mean(closes[-10:])
        ma5 = self._safe_mean(closes[-5:])
        price_vs_ma20 = (latest - ma20) / max(ma20, 0.01)

        ma10_above_ma20_days = 0
        ma5_above_ma10_days = 0
        for idx in range(len(closes) - 1, max(len(closes) - 20, 1), -1):
            ma10_i = self._safe_mean(closes[max(0, idx - 9) : idx + 1])
            ma20_i = self._safe_mean(closes[max(0, idx - 19) : idx + 1])
            ma5_i = self._safe_mean(closes[max(0, idx - 4) : idx + 1])
            if ma10_i >= ma20_i:
                ma10_above_ma20_days += 1
            if ma5_i >= ma10_i:
                ma5_above_ma10_days += 1

        if ret40 >= 0.8 or (latest - prev) / max(prev, 0.01) >= 0.08:
            trend_class: TrendClass = "B"
        elif ret40 >= 0.3 and vol_slope20 > 0:
            trend_class = "A_B"
        elif ret40 >= 0.12:
            trend_class = "A"
        else:
            trend_class = "Unknown"

        if ret40 < 0.3:
            stage: Stage = "Early"
        elif ret40 <= 0.8:
            stage = "Mid"
        else:
            stage = "Late"

        if ret40 >= 0.8:
            theme_stage: ThemeStage = "高潮"
        elif vol_slope20 > 0 and up_down_volume_ratio >= 1.1:
            theme_stage = "发酵中"
        elif ret40 <= 0.05 and retrace20 > 0.15:
            theme_stage = "退潮"
        else:
            theme_stage = "Unknown"

        has_blowoff_top = False
        for idx in range(max(0, len(closes) - 20), len(closes)):
            if volumes[idx] > max(avg_vol10, 1.0) * 2.5 and closes[idx] <= opens[idx]:
                has_blowoff_top = True
                break

        has_divergence_5d = False
        if len(closes) >= 10:
            price_rise = closes[-1] > closes[-6]
            avg_v5 = self._safe_mean(volumes[-5:])
            avg_prev5 = self._safe_mean(volumes[-10:-5])
            has_divergence_5d = price_rise and avg_v5 < avg_prev5 * 0.9

        has_upper_shadow_risk = False
        for idx in range(max(0, len(closes) - 5), len(closes)):
            bar_range = highs[idx] - lows[idx]
            if bar_range <= 0:
                continue
            body_high = max(opens[idx], closes[idx])
            upper_shadow = highs[idx] - body_high
            if upper_shadow / bar_range > 0.5 and closes[idx] <= opens[idx]:
                has_upper_shadow_risk = True
                break

        pseudo_float_shares = 2_000_000_000.0 + (self._hash_seed(symbol) % 500) * 10_000_000.0
        turnover20 = self._safe_mean(
            [max(0.0, float(vol)) / pseudo_float_shares for vol in volumes[start20:]]
        )

        score_raw = (
            45
            + ret40 * 95
            + up_down_volume_ratio * 8
            - pullback_volume_ratio * 14
            + max(0.0, (0.08 - abs(price_vs_ma20)) * 180)
        )
        score = int(round(max(0.0, min(100.0, score_raw))))
        ai_confidence = max(
            0.35,
            min(
                0.95,
                0.50
                + ret40 * 0.30
                + (up_down_volume_ratio - 1.0) * 0.10
                - max(0.0, pullback_volume_ratio - 0.8) * 0.20,
            ),
        )
        has_approx = any(point.price_source == "approx" for point in candles)

        return ScreenerResult(
            symbol=symbol,
            name=self._resolve_symbol_name(symbol),
            latest_price=round(latest, 2),
            day_change=round(latest - prev, 2),
            day_change_pct=round((latest - prev) / max(prev, 0.01), 4),
            score=score,
            ret40=round(ret40, 4),
            turnover20=round(turnover20, 4),
            amount20=float(amount20),
            amplitude20=round(amplitude20, 4),
            retrace20=round(retrace20, 4),
            pullback_days=pullback_days,
            ma10_above_ma20_days=ma10_above_ma20_days,
            ma5_above_ma10_days=ma5_above_ma10_days,
            price_vs_ma20=round(price_vs_ma20, 4),
            vol_slope20=round(vol_slope20, 4),
            up_down_volume_ratio=round(up_down_volume_ratio, 4),
            pullback_volume_ratio=round(pullback_volume_ratio, 4),
            has_blowoff_top=has_blowoff_top,
            has_divergence_5d=has_divergence_5d,
            has_upper_shadow_risk=has_upper_shadow_risk,
            ai_confidence=round(ai_confidence, 2),
            theme_stage=theme_stage,
            trend_class=trend_class,
            stage=stage,
            labels=["K线上下文补全"],
            reject_reasons=[],
            degraded=has_approx,
            degraded_reason="MINUTE_DATA_MISSING_PARTIAL" if has_approx else None,
        )

    def _build_ai_context_text(self, symbol: str, row: ScreenerResult | None) -> str:
        candles = self._ensure_candles(symbol)
        if not candles:
            return "kline=unavailable"

        closes = [item.close for item in candles]
        latest = closes[-1]
        look20 = min(20, len(closes) - 1)
        ret20 = 0.0
        if look20 > 0:
            start_price = closes[-look20]
            ret20 = (latest - start_price) / max(start_price, 0.01)
        ma20 = self._safe_mean(closes[-20:])
        annotation = self._annotation_store.get(symbol)
        annotation_text = "manual=none"
        if annotation:
            annotation_text = (
                f"manual_start={annotation.start_date}, manual_stage={annotation.stage}, "
                f"manual_trend={annotation.trend_class}, decision={annotation.decision}"
            )
        row_text = "row=none"
        if row:
            row_text = (
                f"row_ret40={row.ret40:.4f}, row_stage={row.stage}, row_trend={row.trend_class}, "
                f"row_theme={row.theme_stage}, row_score={row.score}"
            )
        return (
            f"latest={latest:.2f}, ret20={ret20:.4f}, price_vs_ma20={(latest - ma20) / max(ma20, 0.01):.4f}, "
            f"{row_text}, {annotation_text}"
        )

    def _guess_theme_name(self, symbol: str, row: ScreenerResult | None) -> str:
        if row and row.theme_stage == "高潮":
            return "主线热点"
        if row and row.theme_stage == "发酵中":
            return "潜在热点"
        return "Unknown"

    def _infer_theme_from_web_evidence(self, evidence: list[dict[str, str]]) -> str:
        if not evidence:
            return "Unknown"
        corpus = " ".join(
            f"{item.get('title', '')} {item.get('snippet', '')}" for item in evidence
        ).lower()
        theme_keywords: list[tuple[str, tuple[str, ...]]] = [
            ("创新药", ("创新药", "医药", "制药", "生物")),
            ("机器人", ("机器人", "自动化", "工业母机")),
            ("半导体", ("芯片", "半导体", "算力")),
            ("新能源", ("锂电", "电池", "光伏", "储能", "新能源")),
            ("军工", ("军工", "航天", "航空", "卫星")),
            ("高端消费", ("消费", "白酒", "食品饮料")),
            ("化工", ("化工", "材料", "涨价")),
        ]
        best_label = "Unknown"
        best_score = 0
        for label, words in theme_keywords:
            score = sum(corpus.count(word.lower()) for word in words)
            if score > best_score:
                best_score = score
                best_label = label
        return best_label if best_score > 0 else "Unknown"

    def _infer_sector_from_context(
        self,
        symbol: str,
        stock_name: str,
        evidence: list[dict[str, str]],
    ) -> str:
        profile = self._fetch_quote_profile(symbol)
        profile_industry = TextProcessor.clean_whitespace(profile.get("industry", ""))
        if profile_industry:
            return profile_industry
        corpus = " ".join(
            [stock_name, symbol, *[f"{item.get('title', '')} {item.get('snippet', '')}" for item in evidence]]
        ).lower()
        rules: list[tuple[str, tuple[str, ...]]] = [
            ("通信设备", ("通信", "运营商", "算力", "光模块", "网络", "信息")),
            ("创新药", ("创新药", "医药", "制药", "生物")),
            ("机器人", ("机器人", "工业自动化", "机器视觉", "工业母机")),
            ("半导体", ("芯片", "半导体", "算力", "封装", "光刻")),
            ("新能源", ("锂电", "电池", "光伏", "储能", "风电", "新能源")),
            ("军工", ("军工", "航天", "航空", "卫星", "雷达")),
            ("消费", ("消费", "食品饮料", "白酒", "家电")),
            ("化工", ("化工", "材料", "涨价", "聚酯", "化纤")),
        ]
        best_label = "Unknown"
        best_score = 0
        for label, words in rules:
            score = sum(corpus.count(word.lower()) for word in words)
            if score > best_score:
                best_score = score
                best_label = label
        return best_label if best_score > 0 else "Unknown"

    def _pick_breakout_hotspot_titles(
        self,
        evidence: list[dict[str, str]],
        breakout_date: str | None,
        *,
        max_items: int = 2,
    ) -> list[str]:
        if not evidence:
            return []
        parsed_breakout = self._parse_date(breakout_date or "")
        with_parsed: list[tuple[dict[str, str], datetime | None]] = [
            (item, self._parse_rss_pub_date(str(item.get("pub_date", ""))))
            for item in evidence
        ]
        selected: list[dict[str, str]] = []
        if parsed_breakout is not None:
            start = parsed_breakout - timedelta(days=5)
            end = parsed_breakout + timedelta(days=10)
            for item, parsed in with_parsed:
                if parsed is None:
                    continue
                if start <= parsed <= end:
                    selected.append(item)
                if len(selected) >= max_items:
                    break
        if parsed_breakout is None and not selected:
            selected = [item for item, _ in with_parsed[:max_items]]
        if parsed_breakout is not None and not selected:
            return []

        titles: list[str] = []
        for item in selected[:max_items]:
            title = self._clean_event_text(str(item.get("title", "")))
            if not title:
                continue
            titles.append(title)
        return titles

    @staticmethod
    def _infer_rise_reasons_from_web_evidence(evidence: list[dict[str, str]]) -> list[str]:
        reasons: list[str] = []
        seen_titles: set[str] = set()
        for item in evidence[:6]:
            title = InMemoryStore._clean_event_text(item.get("title", ""))
            if title and title not in seen_titles:
                seen_titles.add(title)
                reasons.append(title)
            if len(reasons) >= 4:
                break
        return InMemoryStore._normalize_rise_reasons(reasons)

    def _infer_breakout_date(self, symbol: str, row: ScreenerResult | None) -> str:
        candles = self._ensure_candles(symbol)
        breakout_index = self._infer_breakout_index_from_candles(candles)
        if breakout_index is not None:
            return candles[breakout_index].time

        if row is None:
            fallback = self._days_ago(18 + self._hash_seed(symbol) % 18)
            return self._align_date_to_candles(candles, fallback)

        offset = 12 + int(max(0.0, min(50.0, row.retrace20 * 150)))
        if row.trend_class in ("A", "A_B"):
            offset += 8
        elif row.trend_class == "B":
            offset = max(6, offset - 4)
        fallback = self._days_ago(offset)
        return self._align_date_to_candles(candles, fallback)

    def _infer_rise_reasons(self, row: ScreenerResult | None) -> list[str]:
        if row is None:
            return ["缺少可用日线数据，需先补齐行情后再评估"]
        reasons: list[str] = []
        if row.vol_slope20 > 0:
            reasons.append("20日量能斜率为正，成交活跃度提升")
        if row.up_down_volume_ratio >= 1.2:
            reasons.append("上涨日量能显著大于下跌日，资金承接较强")
        if row.pullback_volume_ratio <= 0.75:
            reasons.append("回调阶段缩量，抛压释放相对充分")
        if row.price_vs_ma20 >= 0:
            reasons.append("价格站上MA20，趋势结构保持多头")
        if row.theme_stage in ("发酵中", "高潮"):
            reasons.append(f"题材阶段处于{row.theme_stage}，具备跟踪价值")
        if not reasons:
            reasons.append("量价结构中性，建议结合分时确认是否介入")
        return reasons[:4]

    def _heuristic_ai_analysis(
        self,
        symbol: str,
        row: ScreenerResult | None,
        source_urls: list[str],
    ) -> AIAnalysisRecord:
        provider = self._config.ai_provider
        stock_name = self._resolve_symbol_name(symbol, row)
        breakout_date = self._infer_breakout_date(symbol, row)
        trend_bull_type = self._trend_bull_type_label(row.trend_class) if row else "Unknown"
        board_label = self._market_board_label(symbol)
        sector_label = self._infer_sector_from_context(symbol, stock_name, [])
        theme_name = self._guess_theme_name(symbol, row)
        if theme_name == "Unknown" and sector_label != "Unknown":
            theme_name = sector_label
        rise_reasons = self._infer_rise_reasons(row)
        if row is None:
            return AIAnalysisRecord(
                provider=provider,
                symbol=symbol,
                name=stock_name,
                fetched_at=self._now_datetime(),
                source_urls=source_urls,
                summary="已尝试补全上下文但可用行情不足，建议先确认近20日量价结构再决定是否介入。",
                conclusion="Unknown",
                confidence=0.5,
                breakout_date=breakout_date,
                trend_bull_type=trend_bull_type,
                theme_name=theme_name,
                rise_reasons=rise_reasons,
                error_code="AI_KLINE_CONTEXT_MISSING",
            )

        ret_text = f"{row.ret40 * 100:.2f}%"
        retrace_text = f"{row.retrace20 * 100:.2f}%"
        turnover_text = f"{row.turnover20 * 100:.2f}%"
        if row.trend_class == "B" and row.retrace20 > 0.12:
            conclusion = "退潮"
        elif row.ai_confidence >= 0.72 and row.up_down_volume_ratio >= 1.2:
            conclusion = "发酵中"
        else:
            conclusion = "高潮" if row.theme_stage == "高潮" else "发酵中"

        summary = (
            f"所属板块 {board_label}，趋势类型 {row.trend_class}，阶段 {row.stage}，窗口涨幅 {ret_text}，"
            f"回撤 {retrace_text}，20日平均换手 {turnover_text}。"
            "建议结合分时承接与板块联动确认节奏。"
        )
        confidence = max(0.45, min(0.95, row.ai_confidence))
        return AIAnalysisRecord(
            provider=provider,
            symbol=symbol,
            name=stock_name,
            fetched_at=self._now_datetime(),
            source_urls=source_urls,
            summary=summary,
            conclusion=conclusion,
            confidence=round(confidence, 2),
            breakout_date=breakout_date,
            trend_bull_type=trend_bull_type,
            theme_name=theme_name,
            rise_reasons=rise_reasons,
            error_code=None,
        )

    def _extract_conclusion_from_text(self, text: str) -> str:
        if "退潮" in text:
            return "退潮"
        if "高潮" in text:
            return "高潮"
        if "发酵" in text:
            return "发酵中"
        return "Unknown"

    def _extract_confidence_from_text(self, text: str, fallback: float) -> float:
        match = re.search(r"(\d{1,3}(?:\.\d+)?%)|(0?\.\d+)", text)
        if not match:
            return fallback
        token = match.group(0)
        if token.endswith("%"):
            value = float(token[:-1]) / 100.0
        else:
            value = float(token)
        return max(0.0, min(1.0, value))

    def _compose_ai_prompt_context(
        self,
        symbol: str,
        row: ScreenerResult | None,
        source_urls: list[str],
    ) -> dict[str, object]:
        baseline = self._heuristic_ai_analysis(symbol, row, source_urls)
        row_text = "no_recent_context"
        if row:
            row_text = (
                f"trend={row.trend_class}, stage={row.stage}, ret={row.ret40:.4f}, "
                f"turnover20={row.turnover20:.4f}, retrace20={row.retrace20:.4f}, "
                f"vol_ratio={row.up_down_volume_ratio:.4f}, theme={row.theme_stage}"
            )
        context_text = self._build_ai_context_text(symbol, row)
        stock_name = self._resolve_symbol_name(symbol, row)
        board_label = self._market_board_label(symbol)
        web_evidence = self._collect_web_evidence(
            symbol,
            stock_name,
            source_urls,
            focus_date=baseline.breakout_date,
        )
        evidence_urls = [item["url"] for item in web_evidence if item.get("url")]
        inferred_sector = self._infer_sector_from_context(symbol, stock_name, web_evidence)
        industry_evidence = self._collect_industry_evidence(
            inferred_sector,
            source_urls,
            focus_date=baseline.breakout_date,
        )
        industry_event_candidates = self._extract_industry_event_candidates(
            inferred_sector,
            industry_evidence,
        )
        if not industry_event_candidates:
            industry_event_candidates = self._build_industry_fallback_reasons(inferred_sector, row)
        evidence_urls.extend([item["url"] for item in industry_evidence if item.get("url")])
        hotspot_titles = self._pick_breakout_hotspot_titles(web_evidence, baseline.breakout_date, max_items=2)
        core_event_candidates = self._extract_core_event_candidates(web_evidence)
        evidence_text = "\n".join(
            [
                (
                    f"- [{idx + 1}] title={self._clean_event_text(str(item.get('title', '')))}; "
                    f"date={item.get('pub_date', '')}; "
                    f"source={item.get('source_name', '')}; "
                    f"url={item.get('url', '')}"
                )
                for idx, item in enumerate(web_evidence)
            ]
        )
        if not evidence_text:
            evidence_text = "no_high_signal_web_evidence"
        industry_evidence_text = "\n".join(
            [
                (
                    f"- [{idx + 1}] title={self._clean_event_text(str(item.get('title', '')))}; "
                    f"date={item.get('pub_date', '')}; "
                    f"source={item.get('source_name', '')}; "
                    f"url={item.get('url', '')}"
                )
                for idx, item in enumerate(industry_evidence)
            ]
        )
        if not industry_evidence_text:
            industry_evidence_text = "no_industry_evidence"
        candles = self._ensure_candles(symbol)
        trading_dates_tail = ",".join([point.time for point in candles[-45:]]) if candles else ""
        baseline_breakout = baseline.breakout_date or self._now_date()
        today = self._now_date()
        recent_kline = self._build_recent_price_volume_snapshot(candles, lookback=16)
        breakout_candidate_details = self._collect_volume_price_breakout_candidates(candles, lookback=55, max_items=4)
        breakout_candidates: list[str] = []
        breakout_candidates_text_parts: list[str] = []
        for idx, day_ret, vol_ratio10, is_breakout, is_washout_reversal in breakout_candidate_details:
            day = candles[idx].time
            breakout_candidates.append(day)
            breakout_candidates_text_parts.append(
                (
                    f"{day}"
                    f"(pct={day_ret * 100:.2f}%,vol10x={vol_ratio10:.2f},"
                    f"break20={1 if is_breakout else 0},washout={1 if is_washout_reversal else 0})"
                )
            )
        if not breakout_candidates:
            breakout_candidates = [baseline_breakout]
            breakout_candidates_text_parts = [f"{baseline_breakout}(baseline)"]
        breakout_candidates_text = ", ".join(breakout_candidates_text_parts)

        prompt = (
            "你是A股短线量价分析助手。只输出 JSON，不要任何解释。\n"
            "JSON keys 固定为: conclusion, confidence, summary, breakout_date, rise_reasons, trend_bull_type, theme_name。\n"
            "任务只做两件事：\n"
            "A) 从候选交易日中选出“当前这一轮”的起爆日 breakout_date。\n"
            "B) 给出 1~2 条上涨原因 rise_reasons（优先公司事件，缺失时给行业驱动）。\n"
            "硬约束：\n"
            "1) breakout_date 必须从 breakout_candidates 中选择。\n"
            "2) 起爆日优先满足量价共振：当日涨幅>=4%、当日成交量>=前10日均量1.5倍，且突破近20日高点或结束盘整。\n"
            "3) 若历史有上一轮炒作且中间有明显盘整，必须选择新一轮起爆日，不得回到旧周期。\n"
            "4) rise_reasons 每条<=26字，禁止媒体名/网址/其他股票代码。\n"
            "5) 若个股无明确利好，rise_reasons 第一条必须写“行业驱动：...”。\n"
            "6) summary 仅一句话，<=40字。\n"
            f"symbol={symbol}\n"
            f"name={stock_name}\n"
            f"board={board_label}\n"
            f"inferred_sector={inferred_sector}\n"
            f"today={today}\n"
            f"features={row_text}\n"
            f"context={context_text}\n"
            f"baseline_breakout={baseline_breakout}\n"
            f"breakout_candidates={breakout_candidates_text}\n"
            f"recent_kline=\n{recent_kline}\n"
            f"core_event_candidates={core_event_candidates}\n"
            f"industry_event_candidates={industry_event_candidates}\n"
            f"breakout_hotspot_titles={hotspot_titles}\n"
            f"configured_sources={source_urls}\n"
            f"trading_dates_tail={trading_dates_tail}\n"
            f"web_evidence_count={len(web_evidence)}\n"
            f"web_evidence=\n{evidence_text}\n"
            f"industry_evidence_count={len(industry_evidence)}\n"
            f"industry_evidence=\n{industry_evidence_text}"
        )
        evidence_urls = list(dict.fromkeys([url for url in evidence_urls if url]))
        return {
            "prompt": prompt,
            "baseline": baseline,
            "web_evidence": web_evidence,
            "evidence_urls": evidence_urls,
            "inferred_sector": inferred_sector,
            "board_label": board_label,
            "hotspot_titles": hotspot_titles,
            "core_event_candidates": core_event_candidates,
            "industry_event_candidates": industry_event_candidates,
            "breakout_candidates": breakout_candidates,
            "industry_evidence": industry_evidence,
        }

    def _call_provider_for_stock(
        self,
        symbol: str,
        row: ScreenerResult | None,
        source_urls: list[str],
    ) -> AIAnalysisRecord | None:
        provider = self._active_ai_provider()
        api_key = self._resolve_provider_api_key(provider)
        if provider is None or not provider.base_url.strip() or not provider.model.strip():
            return None
        if not api_key:
            return None

        prompt_ctx = self._compose_ai_prompt_context(symbol, row, source_urls)
        baseline = prompt_ctx["baseline"]  # type: ignore[assignment]
        web_evidence = prompt_ctx["web_evidence"]  # type: ignore[assignment]
        evidence_urls = prompt_ctx["evidence_urls"]  # type: ignore[assignment]
        industry_evidence = (
            prompt_ctx.get("industry_evidence")
            if isinstance(prompt_ctx.get("industry_evidence"), list)
            else []
        )
        inferred_sector = str(prompt_ctx.get("inferred_sector", "")).strip() or "Unknown"
        board_label = str(prompt_ctx.get("board_label", "")).strip() or self._market_board_label(symbol)
        core_event_candidates = (
            prompt_ctx.get("core_event_candidates")
            if isinstance(prompt_ctx.get("core_event_candidates"), list)
            else []
        )
        industry_event_candidates = (
            prompt_ctx.get("industry_event_candidates")
            if isinstance(prompt_ctx.get("industry_event_candidates"), list)
            else []
        )
        breakout_candidates = (
            prompt_ctx.get("breakout_candidates")
            if isinstance(prompt_ctx.get("breakout_candidates"), list)
            else []
        )
        prompt = str(prompt_ctx["prompt"])
        if not isinstance(baseline, AIAnalysisRecord):
            baseline = self._heuristic_ai_analysis(symbol, row, source_urls)
        if not isinstance(web_evidence, list):
            web_evidence = []
        if not isinstance(evidence_urls, list):
            evidence_urls = []
        if not isinstance(industry_evidence, list):
            industry_evidence = []
        combined_evidence: list[dict[str, str]] = []
        combined_evidence.extend(web_evidence)
        combined_evidence.extend(industry_evidence)
        stock_name = self._resolve_symbol_name(symbol, row)
        body = {
            "model": provider.model,
            "temperature": 0.0,
            "messages": [
                {"role": "system", "content": "You are an A-share short-term trend analyst. Output JSON only."},
                {"role": "user", "content": prompt},
            ],
        }

        try:
            attempts = max(1, int(self._config.ai_retry_count) + 1)
            last_error: Exception | None = None
            payload: dict[str, object] = {}
            for attempt in range(attempts):
                try:
                    with httpx.Client(timeout=float(self._config.ai_timeout_sec)) as client:
                        response = client.post(
                            f"{provider.base_url.rstrip('/')}/chat/completions",
                            headers={"Authorization": f"Bearer {api_key}"},
                            json=body,
                        )
                    response.raise_for_status()
                    payload = response.json()
                    break
                except Exception as exc:
                    last_error = exc
                    if attempt + 1 >= attempts:
                        raise
            if not payload:
                raise last_error or ValueError("AI_EMPTY_PAYLOAD")
            content = (
                payload.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
                .strip()
            )
            if not content:
                raise ValueError("AI_EMPTY_CONTENT")

            fallback_confidence = row.ai_confidence if row else 0.6
            parsed: dict[str, object] | None = None
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError:
                json_match = re.search(r"\{[\s\S]+\}", content)
                if json_match:
                    try:
                        parsed = json.loads(json_match.group(0))
                    except json.JSONDecodeError:
                        parsed = None

            conclusion_raw = (
                str(parsed.get("conclusion", "")).strip()
                if parsed
                else self._extract_conclusion_from_text(content)
            ) or self._extract_conclusion_from_text(content)
            conclusion = normalize_ai_conclusion(conclusion_raw)
            confidence = (
                float(parsed.get("confidence", fallback_confidence))
                if parsed
                else self._extract_confidence_from_text(content, fallback_confidence)
            )
            confidence = max(0.0, min(1.0, float(confidence)))
            summary = (
                str(parsed.get("summary", "")).strip() if parsed else content.replace("\n", " ").strip()
            )
            if not summary:
                summary = baseline.summary
            if len(summary) > 120:
                summary = f"{summary[:120]}..."

            breakout_raw = str(parsed.get("breakout_date", "")).strip() if parsed else ""
            breakout_date = self._sanitize_breakout_date(
                symbol,
                breakout_raw,
                baseline.breakout_date or self._now_date(),
            )
            if breakout_candidates:
                candles = self._ensure_candles(symbol)
                normalized_candidates = {
                    self._align_date_to_candles(candles, str(item))
                    for item in breakout_candidates
                    if str(item).strip()
                }
                normalized_candidates.discard("")
                if normalized_candidates and breakout_date not in normalized_candidates:
                    breakout_date = sorted(normalized_candidates)[0]

            trend_bull_type = str(parsed.get("trend_bull_type", "")).strip() if parsed else ""
            if not trend_bull_type:
                trend_bull_type = baseline.trend_bull_type or "Unknown"

            theme_name = str(parsed.get("theme_name", "")).strip() if parsed else ""
            if not theme_name:
                theme_name = baseline.theme_name or "未知题材"

            rise_reasons: list[str] = []
            if parsed and isinstance(parsed.get("rise_reasons"), list):
                rise_reasons = [str(item).strip() for item in parsed["rise_reasons"] if str(item).strip()]
            if not rise_reasons:
                rise_reasons = baseline.rise_reasons
            rise_reasons = self._sanitize_ai_rise_reasons(
                rise_reasons,
                symbol=symbol,
                core_event_candidates=core_event_candidates,
                industry_event_candidates=industry_event_candidates,
                industry_hint=inferred_sector,
            )

            theme_name = self._sanitize_theme_name(
                theme_name,
                evidence=combined_evidence,
                inferred_sector=inferred_sector,
            )
            if rise_reasons and rise_reasons[0].startswith("行业驱动：") and inferred_sector != "Unknown":
                theme_name = inferred_sector
            summary = self._build_compact_ai_summary(
                symbol=symbol,
                breakout_date=breakout_date,
                board_label=board_label,
                inferred_sector=inferred_sector,
                rise_reasons=rise_reasons,
                raw_summary=summary,
                row=row,
            )

            return AIAnalysisRecord(
                provider=provider.id,
                symbol=symbol,
                name=stock_name,
                fetched_at=self._now_datetime(),
                source_urls=evidence_urls[:8] or source_urls,
                summary=summary,
                conclusion=conclusion,
                confidence=round(confidence, 2),
                breakout_date=breakout_date,
                trend_bull_type=trend_bull_type,
                theme_name=theme_name,
                rise_reasons=rise_reasons[:2],
                error_code=None,
            )
        except httpx.TimeoutException:
            return baseline.model_copy(
                update={
                    "provider": provider.id,
                    "source_urls": evidence_urls[:8] or source_urls,
                    "summary": "AI请求超时，已回退本地规则分析。",
                    "error_code": "AI_TIMEOUT",
                }
            )
        except Exception:
            return baseline.model_copy(
                update={
                    "provider": provider.id,
                    "source_urls": evidence_urls[:8] or source_urls,
                    "summary": "AI请求失败，已回退本地规则分析。",
                    "error_code": "AI_PROVIDER_ERROR",
                }
            )

    def test_ai_provider(
        self,
        provider: AIProviderConfig,
        *,
        fallback_api_key: str = "",
        fallback_api_key_path: str = "",
        timeout_sec: int = 10,
    ) -> AIProviderTestResponse:
        if not provider.base_url.strip() or not provider.model.strip():
            return AIProviderTestResponse(
                ok=False,
                provider_id=provider.id,
                latency_ms=0,
                message="缺少 base_url 或 model",
                error_code="INVALID_PROVIDER_CONFIG",
            )

        api_key = self._resolve_provider_api_key(
            provider,
            fallback_api_key=fallback_api_key,
            fallback_api_key_path=fallback_api_key_path,
        )
        if not api_key:
            return AIProviderTestResponse(
                ok=False,
                provider_id=provider.id,
                latency_ms=0,
                message="缺少 API 凭证，请填写 api_key 或 api_key_path",
                error_code="AI_KEY_MISSING",
            )

        body = {
            "model": provider.model,
            "temperature": 0.0,
            "messages": [
                {"role": "system", "content": "You are a connectivity probe."},
                {"role": "user", "content": "Reply only OK"},
            ],
        }
        started = time.perf_counter()
        try:
            with httpx.Client(timeout=float(timeout_sec)) as client:
                resp = client.post(
                    f"{provider.base_url.rstrip('/')}/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json=body,
                )
            latency_ms = int((time.perf_counter() - started) * 1000)
            resp.raise_for_status()
            payload = resp.json()
            content = (
                payload.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
                .strip()
            )
            if not content:
                return AIProviderTestResponse(
                    ok=False,
                    provider_id=provider.id,
                    latency_ms=latency_ms,
                    message="返回为空，Provider 可能不可用",
                    error_code="AI_EMPTY_CONTENT",
                )
            return AIProviderTestResponse(
                ok=True,
                provider_id=provider.id,
                latency_ms=latency_ms,
                message=f"连接成功，耗时 {latency_ms}ms",
                error_code=None,
            )
        except httpx.TimeoutException:
            latency_ms = int((time.perf_counter() - started) * 1000)
            return AIProviderTestResponse(
                ok=False,
                provider_id=provider.id,
                latency_ms=latency_ms,
                message=f"请求超时（{timeout_sec}s）",
                error_code="AI_TIMEOUT",
            )
        except Exception as exc:
            latency_ms = int((time.perf_counter() - started) * 1000)
            return AIProviderTestResponse(
                ok=False,
                provider_id=provider.id,
                latency_ms=latency_ms,
                message=f"请求失败: {type(exc).__name__}",
                error_code="AI_PROVIDER_ERROR",
            )

    def get_ai_prompt_preview(self, symbol: str) -> dict[str, object]:
        row = self._latest_rows.get(symbol) or self._build_row_from_candles(symbol)
        if row and symbol not in self._latest_rows:
            self._latest_rows[symbol] = row
        source_urls = self._enabled_ai_source_urls()
        prompt_ctx = self._compose_ai_prompt_context(symbol, row, source_urls)
        baseline = prompt_ctx.get("baseline")
        return {
            "symbol": symbol,
            "name": self._resolve_symbol_name(symbol, row),
            "provider": self._config.ai_provider,
            "prompt": str(prompt_ctx.get("prompt", "")),
            "web_evidence_count": len(prompt_ctx.get("web_evidence", []))
            if isinstance(prompt_ctx.get("web_evidence"), list)
            else 0,
            "inferred_sector": str(prompt_ctx.get("inferred_sector", "")),
            "baseline_breakout": baseline.breakout_date if isinstance(baseline, AIAnalysisRecord) else None,
        }

    def _gen_candles(self, seed: int, start_price: float = 40.0) -> list[CandlePoint]:
        points: list[CandlePoint] = []
        close = start_price
        for i in range(119, -1, -1):
            date = self._days_ago(i)
            drift = math.sin((i + seed) / 9.0) * 0.9 + (0.2 if seed % 3 == 0 else 0.35)
            open_price = max(5.0, close + drift * 0.35)
            high = open_price + abs(drift) * 1.8 + 0.6
            low = max(1.0, open_price - abs(drift) * 1.4 - 0.5)
            close = round(low + (high - low) * 0.68, 2)
            points.append(
                CandlePoint(
                    time=date,
                    open=round(open_price, 2),
                    high=round(high, 2),
                    low=round(low, 2),
                    close=close,
                    volume=int(2_000_000 + (math.cos((i + seed) / 7.0) + 1.4) * 1_700_000),
                    amount=float(int(close * 100_000_000)),
                    price_source="approx" if seed % 4 == 0 else "vwap",
                )
            )
        return points

    @staticmethod
    def _candles_runtime_cache_max_symbols() -> int:
        raw = os.getenv("TDX_TREND_CANDLES_RUNTIME_CACHE_MAX_SYMBOLS", "").strip()
        if not raw:
            return 1500
        try:
            value = int(raw)
        except Exception:
            return 1500
        if value <= 0:
            return 0
        return max(50, int(value))

    @staticmethod
    def _candles_runtime_idle_keep_symbols() -> int:
        raw = os.getenv("TDX_TREND_CANDLES_RUNTIME_IDLE_KEEP_SYMBOLS", "").strip()
        if not raw:
            return 200
        try:
            value = int(raw)
        except Exception:
            return 200
        return max(0, int(value))

    def _trim_candles_runtime_cache(self, *, target_max_symbols: int) -> int:
        target = max(0, int(target_max_symbols))
        removed = 0
        with self._candles_lock:
            if target <= 0:
                removed = len(self._candles_map)
                self._candles_map.clear()
                self._candles_signatures.clear()
                return removed
            while len(self._candles_map) > target:
                oldest_key = next(iter(self._candles_map))
                self._candles_map.pop(oldest_key, None)
                self._candles_signatures.pop(oldest_key, None)
                removed += 1
        return removed

    def _ensure_candles(self, symbol: str) -> list[CandlePoint]:
        symbol_key = str(symbol).strip().lower()
        if not re.fullmatch(r"(?:sh|sz|bj)\d{6}", symbol_key):
            raise MarketDataUnavailable("证券代码需为 sh、sz 或 bj 加六位数字。")
        config = self.get_config()
        roots = [self._resolve_user_path(config.tdx_data_path) / symbol_key[:2] / "lday"]
        csv_root = self._resolve_user_path(config.akshare_cache_dir or os.getenv('AKSHARE_CACHE_DIR', '') or str(Path.home() / ".tdx-trend" / "akshare" / "daily"))
        paths = [roots[0] / f"{symbol_key}.day", roots[0] / f"{symbol_key[2:]}.day", csv_root / f"{symbol_key}.csv", csv_root / f"{symbol_key[2:]}.csv"]
        versions = []
        for path in paths:
            try:
                stat = path.stat()
                versions.append((str(path), stat.st_mtime_ns, stat.st_size))
            except OSError:
                versions.append((str(path), None, None))
        signature = (config.tdx_data_path, config.market_data_source, config.akshare_cache_dir, config.candles_window_bars, tuple(versions))
        with self._candles_lock:
            cached = self._candles_map.get(symbol_key)
            if cached is not None and self._candles_signatures.get(symbol_key) == signature:
                # LRU touch: move to tail.
                self._candles_map.pop(symbol_key, None)
                self._candles_map[symbol_key] = cached
                return cached

        window_bars = max(120, int(config.candles_window_bars))
        real_candles = load_candles_for_symbol(
            config.tdx_data_path,
            symbol_key,
            window=window_bars,
            market_data_source=config.market_data_source,
            akshare_cache_dir=config.akshare_cache_dir,
        )
        if real_candles:
            resolved = real_candles
        else:
            with self._candles_lock:
                self._candles_map.pop(symbol_key, None)
                self._candles_signatures.pop(symbol_key, None)
            raise MarketDataUnavailable(f"未找到 {symbol_key} 的真实行情，请检查行情目录或同步数据。")

        with self._lock:
            current = self._config
            fields = ('tdx_data_path', 'market_data_source', 'akshare_cache_dir', 'candles_window_bars')
            if any(getattr(current, field) != getattr(config, field) for field in fields):
                return self._ensure_candles(symbol_key)
            with self._candles_lock:
                existing = self._candles_map.get(symbol_key)
                if existing is not None and self._candles_signatures.get(symbol_key) == signature:
                    self._candles_map.pop(symbol_key, None)
                    self._candles_map[symbol_key] = existing
                    return existing

                self._candles_map[symbol_key] = resolved
                self._candles_signatures[symbol_key] = signature
                max_symbols = self._candles_runtime_cache_max_symbols()
                if max_symbols > 0 and len(self._candles_map) > max_symbols:
                    trim_to = max(1, int(max_symbols * 0.9))
                    while len(self._candles_map) > trim_to:
                        oldest_key = next(iter(self._candles_map))
                        self._candles_map.pop(oldest_key, None)
                        self._candles_signatures.pop(oldest_key, None)
                return resolved

    @staticmethod
    def _intraday_axis() -> list[str]:
        morning = []
        for i in range(120):
            total = 9 * 60 + 30 + i
            hh = total // 60
            mm = total % 60
            morning.append(f"{hh:02d}:{mm:02d}")
        afternoon = []
        for i in range(120):
            total = 13 * 60 + i
            hh = total // 60
            mm = total % 60
            afternoon.append(f"{hh:02d}:{mm:02d}")
        return morning + afternoon

    def _gen_intraday_points(self, symbol: str, date: str, base_price: float) -> list[IntradayPoint]:
        times = self._intraday_axis()
        seed = self._hash_seed(f"{symbol}-{date}")

        points: list[IntradayPoint] = []
        price = base_price
        turnover = 0.0
        total_volume = 0

        for index, time in enumerate(times):
            wave = math.sin((index + seed) / 10.0) * 0.22
            drift = (index / len(times) - 0.5) * (0.5 if seed % 3 == 0 else 0.28)
            micro = math.cos((index + seed) / 5.0) * 0.06
            price = max(1.0, price + wave * 0.05 + drift * 0.03 + micro)
            rounded_price = round(price, 2)

            volume = max(1200, int(4000 + math.sin((index + seed) / 8.0) * 1300 + (seed % 7) * 180))
            total_volume += volume
            turnover += rounded_price * volume
            avg_price = round(turnover / total_volume, 2)

            points.append(
                IntradayPoint(
                    time=time,
                    price=rounded_price,
                    avg_price=avg_price,
                    volume=volume,
                    price_source="approx" if index % 79 == 0 else "vwap",
                )
            )

        return points

    @staticmethod
    def _mock_symbol(index: int) -> str:
        market = "sh" if index % 2 == 0 else "sz"
        code = str(100000 + index).zfill(6)[-6:]
        return f"{market}{code}"

    @staticmethod
    def _mock_name(index: int) -> str:
        sectors = ["科技", "医药", "消费", "金融", "能源", "制造", "材料", "军工"]
        return f"{sectors[index % len(sectors)]}样本{index + 1}"

    def _pool_record(
        self,
        index: int,
        mode: ScreenerMode,
        stage: Literal["input", "step1", "step2", "step3", "step4"],
    ) -> ScreenerResult:
        strict_offset = 0.0 if mode == "strict" else 0.015
        base_ret = 0.06 + ((index % 200) / 1000.0) + strict_offset
        trend: TrendClass = "B" if index % 17 == 0 else "A_B" if index % 5 == 0 else "A"
        stage_label: Stage = "Early" if index % 3 == 0 else "Mid" if index % 3 == 1 else "Late"
        degraded = stage != "input" and index % 211 == 0
        theme_stage = THEME_STAGES[index % len(THEME_STAGES)]

        return ScreenerResult(
            symbol=self._mock_symbol(index),
            name=self._mock_name(index),
            latest_price=round(8.0 + (index % 220) * 0.9, 2),
            day_change=round(-2.1 + (index % 11) * 0.42, 2),
            day_change_pct=round(-0.03 + (index % 15) * 0.0045, 4),
            score=max(20, 92 - (index % 70)),
            ret40=base_ret,
            turnover20=0.035 + (index % 25) * 0.002,
            amount20=220_000_000 + (index % 150) * 18_000_000,
            amplitude20=0.025 + (index % 12) * 0.002,
            retrace20=0.03 + (index % 22) * 0.01,
            pullback_days=1 + (index % 6),
            ma10_above_ma20_days=4 + (index % 11),
            ma5_above_ma10_days=2 + (index % 9),
            price_vs_ma20=-0.03 + (index % 13) * 0.008,
            vol_slope20=-0.2 + (index % 20) * 0.07,
            up_down_volume_ratio=0.9 + (index % 18) * 0.06,
            pullback_volume_ratio=0.45 + (index % 11) * 0.08,
            has_blowoff_top=stage != "input" and index % 31 == 0,
            has_divergence_5d=stage != "input" and index % 17 == 0,
            has_upper_shadow_risk=stage != "input" and index % 19 == 0,
            ai_confidence=0.4 + (index % 11) * 0.05,
            theme_stage=theme_stage,
            trend_class=trend,
            stage=stage_label,
            labels=["全市场候选"] if stage == "input" else ["活跃", "趋势延续"],
            reject_reasons=[],
            degraded=degraded,
            degraded_reason="PARTIAL_CACHE_FALLBACK" if degraded else None,
        )

    @staticmethod
    def _build_result(item: dict[str, str], index: int, mode: ScreenerMode) -> ScreenerResult:
        mode_offset = 0 if mode == "strict" else 4
        score = 86 - index * 4 + mode_offset
        degraded = item["symbol"] == "sz002230"
        theme_stage = THEME_STAGES[index % len(THEME_STAGES)]

        trend_class: TrendClass = item["trend"]  # type: ignore[assignment]
        stage: Stage = item["stage"]  # type: ignore[assignment]

        return ScreenerResult(
            symbol=item["symbol"],
            name=item["name"],
            latest_price=round(42 + index * 3.6, 2),
            day_change=round(-1.2 + (index % 5) * 0.8, 2),
            day_change_pct=round(-0.018 + (index % 6) * 0.009, 4),
            score=score,
            ret40=0.22 + index * 0.031,
            turnover20=0.053 + index * 0.008,
            amount20=580_000_000 + index * 80_000_000,
            amplitude20=0.032 + index * 0.003,
            retrace20=0.06 + index * 0.02,
            pullback_days=1 + (index % 4),
            ma10_above_ma20_days=8 + (index % 7),
            ma5_above_ma10_days=6 + (index % 5),
            price_vs_ma20=0.008 + (index % 6) * 0.005,
            vol_slope20=0.14 + (index % 8) * 0.05,
            up_down_volume_ratio=1.26 + (index % 6) * 0.1,
            pullback_volume_ratio=0.5 + (index % 5) * 0.07,
            has_blowoff_top=index % 21 == 0,
            has_divergence_5d=index % 13 == 0,
            has_upper_shadow_risk=index % 17 == 0,
            ai_confidence=0.63 + (index % 4) * 0.08,
            theme_stage=theme_stage,
            trend_class=trend_class,
            stage=stage,
            labels=["活跃", "高波动" if trend_class == "B" else "趋势延续"],
            reject_reasons=[],
            degraded=degraded,
            degraded_reason="FLOAT_SHARES_CACHE_USED" if degraded else None,
        )

    def _pool_range(
        self,
        start: int,
        count: int,
        mode: ScreenerMode,
        stage: Literal["input", "step1", "step2", "step3"],
    ) -> list[ScreenerResult]:
        return [self._pool_record(start + i, mode, stage) for i in range(count)]

    @staticmethod
    def _build_screener_run_id() -> str:
        return f"{int(datetime.now().timestamp() * 1000)}-{uuid4().hex[:6]}"

    @state_locked
    def _store_screener_run_detail(self, detail: ScreenerRunDetail, *, persist: bool = True) -> ScreenerRunDetail:
        latest_rows: dict[str, ScreenerResult] = {}
        for row in detail.step_pools.input:
            latest_rows[row.symbol] = row
        for row in detail.step_pools.step1:
            latest_rows[row.symbol] = row
        for row in detail.step_pools.step2:
            latest_rows[row.symbol] = row
        for row in detail.step_pools.step3:
            latest_rows[row.symbol] = row
        for row in detail.step_pools.step4:
            latest_rows[row.symbol] = row
        self._latest_rows = latest_rows
        self._run_store[detail.run_id] = detail
        if persist:
            self._persist_app_state()
        return detail

    def _is_screener_result_cache_enabled(self) -> bool:
        return self._env_flag("TDX_TREND_SCREENER_RESULT_CACHE", True)

    @staticmethod
    def _screener_input_pool_load_timeout_sec() -> float | None:
        raw = os.getenv("TDX_TREND_SCREENER_INPUT_POOL_LOAD_TIMEOUT_SEC", "").strip()
        if not raw:
            return 120.0
        try:
            parsed = float(raw)
        except Exception:
            return 120.0
        if parsed <= 0:
            return None
        return parsed

    @staticmethod
    def _screener_result_cache_ttl_sec() -> float:
        raw = os.getenv("TDX_TREND_SCREENER_RESULT_CACHE_TTL_SEC", "").strip()
        if not raw:
            return 24 * 3600.0
        try:
            return max(0.0, float(raw))
        except Exception:
            return 24 * 3600.0

    @staticmethod
    def _is_screener_result_cache_eligible(params: ScreenerParams) -> bool:
        _ = params
        return True

    def _resolve_tdx_last_trade_date(self, tdx_root: str | None = None) -> str:
        root = self._resolve_user_path(tdx_root or self._config.tdx_data_path)
        return str(probe_tdx_latest_trade_date(root) or "").strip() or "__unknown__"

    def _build_screener_result_cache_key(self, params: ScreenerParams) -> str:
        normalized_as_of_date = str(params.as_of_date or "").strip() or "__latest__"
        params_raw = params.model_dump(exclude_none=True)
        params_raw["as_of_date"] = normalized_as_of_date
        payload = {
            "version": self._SCREENER_RESULT_CACHE_VERSION,
            "params": params_raw,
            "config": {
                "tdx_root": str(self._resolve_user_path(self._config.tdx_data_path)),
                "tdx_last_trade_date": self._resolve_tdx_last_trade_date(),
                "market_data_source": str(self._config.market_data_source).strip(),
                "candles_window_bars": int(self._config.candles_window_bars),
            },
            "algo": {
                "wyckoff_algo_version": str(self._wyckoff_event_algo_version).strip(),
                "wyckoff_data_version": str(self._wyckoff_event_data_version).strip(),
            },
        }
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:24]

    def _screener_result_cache_file(self, params: ScreenerParams) -> Path:
        cache_key = self._build_screener_result_cache_key(params)
        return self._resolve_screener_result_cache_dir() / f"{cache_key}.json"

    def _load_screener_result_cache(self, params: ScreenerParams) -> ScreenerRunDetail | None:
        if not self._is_screener_result_cache_enabled():
            return None
        if not self._is_screener_result_cache_eligible(params):
            return None
        path = self._screener_result_cache_file(params)
        if not path.exists():
            return None
        ttl_sec = self._screener_result_cache_ttl_sec()
        if ttl_sec > 0:
            try:
                mtime = path.stat().st_mtime
                age_sec = max(0.0, time.time() - mtime)
                if age_sec > ttl_sec:
                    return None
                # Invalidate if cached data is from a previous day
                if datetime.fromtimestamp(mtime).strftime("%Y-%m-%d") < datetime.now().strftime("%Y-%m-%d"):
                    return None
            except Exception:
                return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                return None
            detail_raw = raw.get("detail")
            if not isinstance(detail_raw, dict):
                return None
            return ScreenerRunDetail(**detail_raw)
        except Exception:
            return None

    def _save_screener_result_cache(self, params: ScreenerParams, detail: ScreenerRunDetail) -> bool:
        if not self._is_screener_result_cache_enabled():
            return False
        if not self._is_screener_result_cache_eligible(params):
            return False
        path = self._screener_result_cache_file(params)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "schema_version": 1,
                "created_at": self._now_datetime(),
                "detail": detail.model_dump(exclude_none=True),
            }
            tmp_path = path.with_suffix(".tmp")
            tmp_path.write_text(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            tmp_path.replace(path)
            return True
        except Exception:
            return False

    def _clone_screener_detail_for_new_run(
        self,
        cached_detail: ScreenerRunDetail,
        *,
        run_id: str,
        params: ScreenerParams,
    ) -> ScreenerRunDetail:
        resolved_step_configs = self._resolve_screener_step_configs(params)
        normalized_params = self._bind_step_configs_to_screener_params(params, resolved_step_configs)
        return cached_detail.model_copy(
            update={
                "run_id": run_id,
                "created_at": self._now_datetime(),
                "as_of_date": normalized_params.as_of_date,
                "params": normalized_params,
                "step_configs": resolved_step_configs,
            }
        )

    def create_screener_run(self, params: ScreenerParams) -> ScreenerRunDetail:
        resolved_step_configs = self._resolve_screener_step_configs(params)
        normalized_params = self._bind_step_configs_to_screener_params(params, resolved_step_configs)
        run_id = self._build_screener_run_id()
        cached_detail = self._load_screener_result_cache(normalized_params)
        if cached_detail is not None:
            detail = self._clone_screener_detail_for_new_run(
                cached_detail,
                run_id=run_id,
                params=normalized_params,
            )
            return self._store_screener_run_detail(detail)

        real_input_pool, real_error, _cache_hit = self._load_input_pool_rows(
            markets=normalized_params.markets,
            return_window_days=normalized_params.return_window_days,
            as_of_date=normalized_params.as_of_date,
        )
        if real_input_pool:
            step1_pool, step2_pool, step3_pool, step4_raw_pool = self._run_screener_filters_for_backtest(
                rows=real_input_pool,
                mode=normalized_params.mode,
                step_configs=resolved_step_configs,
            )
            step4_pool = [
                row.model_copy(update={"labels": list({*row.labels, "待买观察"})})
                for row in step4_raw_pool
            ]
            has_degraded_rows = any(row.degraded for row in real_input_pool)

            detail = ScreenerRunDetail(
                run_id=run_id,
                created_at=self._now_datetime(),
                as_of_date=normalized_params.as_of_date,
                params=normalized_params,
                step_configs=resolved_step_configs,
                step_summary=ScreenerStepSummary(
                    input_count=len(real_input_pool),
                    step1_count=len(step1_pool),
                    step2_count=len(step2_pool),
                    step3_count=len(step3_pool),
                    step4_count=len(step4_pool),
                ),
                step_pools=ScreenerStepPools(
                    input=real_input_pool,
                    step1=step1_pool,
                    step2=step2_pool,
                    step3=step3_pool,
                    step4=step4_pool,
                ),
                results=step4_pool,
                degraded=bool(has_degraded_rows or real_error),
                degraded_reason=real_error,
            )
            self._save_screener_result_cache(normalized_params, detail)
            return self._store_screener_run_detail(detail)

        mode = normalized_params.mode

        input_count = 5100
        rank_start, rank_end = self._normalize_screener_step1_rank_window(
            resolved_step_configs.step1.rank_start,
            resolved_step_configs.step1.top_n,
        )
        step2_count = 68 if mode == "strict" else 92
        step3_count = 26 if mode == "strict" else 37

        input_pool = self._pool_range(0, input_count, mode, "input")
        step1_pool = [
            row.model_copy(update={"score": 78 - (index % 30), "labels": ["活跃强势池"]})
            for index, row in enumerate(input_pool[rank_start - 1 : rank_end][:400])
        ]
        step2_pool = [
            row.model_copy(update={"score": 82 - (index % 28), "labels": ["图形待确认"]})
            for index, row in enumerate(step1_pool[:step2_count])
        ]
        step3_pool = [
            row.model_copy(update={"score": 86 - (index % 20), "labels": ["量能健康"]})
            for index, row in enumerate(step2_pool[:step3_count])
        ]

        final_base = STOCK_POOL[:5] if mode == "strict" else STOCK_POOL
        step4_pool: list[ScreenerResult] = []
        for index, item in enumerate(final_base):
            base_result = self._build_result(item, index, mode)
            if index < len(step3_pool):
                base_result = base_result.model_copy(
                    update={
                        "symbol": step3_pool[index].symbol,
                        "name": step3_pool[index].name,
                    }
                )
            step4_pool.append(base_result.model_copy(update={"labels": ["题材发酵", "待买观察"]}))

        detail = ScreenerRunDetail(
            run_id=run_id,
            created_at=self._now_datetime(),
            as_of_date=normalized_params.as_of_date,
            params=normalized_params,
            step_configs=resolved_step_configs,
            step_summary=ScreenerStepSummary(
                input_count=input_count,
                step1_count=len(step1_pool),
                step2_count=step2_count,
                step3_count=step3_count,
                step4_count=len(step4_pool),
            ),
            step_pools=ScreenerStepPools(
                input=input_pool,
                step1=step1_pool,
                step2=step2_pool,
                step3=step3_pool,
                step4=step4_pool,
            ),
            results=step4_pool,
            degraded=any(item.degraded for item in step4_pool),
            degraded_reason=real_error or "PARTIAL_FLOAT_SHARES_FROM_CACHE"
            if any(item.degraded for item in step4_pool)
            else None,
        )
        return self._store_screener_run_detail(detail)

    def get_screener_run(self, run_id: str) -> ScreenerRunDetail | None:
        return self._run_store.get(run_id)

    def get_latest_screener_run(self) -> ScreenerRunDetail | None:
        if not self._run_store:
            return None
        return max(self._run_store.values(), key=lambda run: run.created_at)

    # ─── B1 Strategy Screener ─────────────────────────────────

    def create_b1_screener_run(
        self,
        *,
        markets: list[str],
        as_of_date: str | None,
        b1_params: Any | None = None,
    ) -> dict[str, Any]:
        import time as _time_mod

        from .core.b1_strategy import B1Params, check_b1

        start = _time_mod.monotonic()

        if b1_params is None:
            bp = B1Params()
        elif isinstance(b1_params, B1Params):
            bp = b1_params
        else:
            bp = B1Params(**b1_params)

        tdx_root = self._config.tdx_data_path
        bars_needed = max(bp.min_total_bars + 50, 300)

        all_symbols: list[tuple[str, str]] = []  # (code, full_symbol)
        for market in markets:
            market_dir = os.path.join(tdx_root, market, "lday")
            if not os.path.isdir(market_dir):
                continue
            for fname in sorted(os.listdir(market_dir)):
                if not fname.endswith(".day"):
                    continue
                code = fname.replace(market, "").replace(".day", "")
                if market == "sh" and not code.startswith("60"):
                    continue
                if market == "sz" and not (code.startswith("00") or code.startswith("30")):
                    continue
                all_symbols.append((code, f"{market}{code}"))

        hits: list[dict[str, Any]] = []
        total_scanned = 0

        for code, full_symbol in all_symbols:
            total_scanned += 1
            try:
                candles = load_candles_for_symbol(
                    tdx_root,
                    full_symbol,
                    window=bars_needed,
                    market_data_source=self._config.market_data_source,
                    akshare_cache_dir=self._config.akshare_cache_dir,
                )
                if candles is None or len(candles) < bp.min_total_bars:
                    continue

                bars = [
                    {
                        "date": cp.time,
                        "open": cp.open,
                        "high": cp.high,
                        "low": cp.low,
                        "close": cp.close,
                        "volume": max(0, int(cp.volume)),
                        "amount": max(0.0, float(cp.amount)),
                    }
                    for cp in candles
                ]

                result = check_b1(code, bars, bp)
                if result is not None:
                    result["name"] = self._resolve_symbol_name(full_symbol)
                    hits.append(result)
            except Exception:
                continue

        elapsed = _time_mod.monotonic() - start
        return {
            "total_scanned": total_scanned,
            "hit_count": len(hits),
            "hits": hits,
            "b1_params": bp,
            "elapsed_sec": round(elapsed, 2),
        }

    @classmethod
    def _resolve_trend_leaders_cache_path(cls) -> Path:
        return Path.home() / ".tdx-trend" / "trend-leaders-daily-cache.json"

    def scan_market_trend_leaders(self, *, request: Any) -> dict[str, Any]:
        from .core.market_momentum import scan_trend_leaders_timeline
        from .models import MarketTrendLeadersRequest

        payload = request if isinstance(request, MarketTrendLeadersRequest) else MarketTrendLeadersRequest.model_validate(request)
        date_to = str(payload.date_to or self._now_date()).strip()
        window_days = int(payload.window_days)
        if payload.date_from:
            date_from = str(payload.date_from).strip()
        else:
            sample_candles = self._ensure_candles("sh600519")
            sample_dates = [item.time for item in sample_candles if item.time <= date_to]
            if len(sample_dates) >= 750:
                date_from = sample_dates[-750]
            elif len(sample_dates) > window_days * 2:
                date_from = sample_dates[-window_days * 2]
            elif sample_dates:
                date_from = sample_dates[0]
            else:
                date_from = date_to

        leaders, series_rows, dates, total_scanned, elapsed, scan_stats = scan_trend_leaders_timeline(
            tdx_root=self._config.tdx_data_path,
            resolve_name=lambda symbol: self._resolve_symbol_name(symbol),
            market_data_source=self._config.market_data_source,
            akshare_cache_dir=self._config.akshare_cache_dir,
            date_from=date_from,
            date_to=date_to,
            window_days=window_days,
            daily_top_n=int(payload.daily_top_n),
            board_filters=list(payload.board_filters or []),
            min_amount_avg=float(payload.min_amount_avg),
            cache_path=str(self._resolve_trend_leaders_cache_path()),
        )
        return {
            "date_from": date_from,
            "date_to": date_to,
            "window_days": window_days,
            "daily_top_n": int(payload.daily_top_n),
            "total_scanned": total_scanned,
            "dates": dates,
            "cache_hits": scan_stats.cache_hits,
            "cache_misses": scan_stats.cache_misses,
            "live_days": scan_stats.live_days,
            "frozen_days": scan_stats.frozen_days,
            "computed_days": scan_stats.computed_days,
            "leaders": [
                {
                    "symbol": row.symbol,
                    "name": row.name,
                    "return_pct": row.return_pct,
                    "window_return_pct": row.window_return_pct,
                    "amount_avg": row.amount_avg,
                    "board": row.board,
                    "leader_days": row.leader_days,
                    "first_leader_date": row.first_leader_date,
                    "last_leader_date": row.last_leader_date,
                    "max_return_pct": row.max_return_pct,
                }
                for row in leaders
            ],
            "series": [
                {
                    "symbol": row.symbol,
                    "name": row.name,
                    "points": [{"date": point.date, "return_pct": point.return_pct} for point in row.points],
                }
                for row in series_rows
            ],
            "elapsed_sec": round(elapsed, 2),
        }

    def _build_abnormal_scan_name_cache(self) -> dict[str, str]:
        """Lightweight symbol names for bulk scan — avoid per-symbol quote lookups."""
        cache: dict[str, str] = {}
        for item in STOCK_POOL:
            symbol = str(item.get("symbol", "")).strip()
            name = str(item.get("name", "")).strip()
            if symbol and name and name.upper() != symbol.upper():
                cache[symbol] = name
        for symbol, row in self._latest_rows.items():
            name = str(row.name or "").strip()
            if symbol and name and name.upper() != symbol.upper():
                cache[symbol] = name
        return cache

    def scan_abnormal_movement(self, *, request: Any) -> dict[str, Any]:
        from .core.abnormal_movement import (
            TRIGGER_THRESHOLD_10,
            TRIGGER_THRESHOLD_30,
            WARN_THRESHOLD_10,
            WARN_THRESHOLD_30,
            scan_abnormal_movement,
        )
        from .models import AbnormalMovementRequest

        payload = (
            request
            if isinstance(request, AbnormalMovementRequest)
            else AbnormalMovementRequest.model_validate(request)
        )
        date_to = str(payload.date_to or self._now_date()).strip()
        if payload.date_from:
            date_from = str(payload.date_from).strip()
        else:
            sample_candles = self._ensure_candles("sh600519")
            sample_dates = [item.time for item in sample_candles if item.time <= date_to]
            date_from = sample_dates[-365] if len(sample_dates) >= 365 else (sample_dates[0] if sample_dates else date_to)

        name_cache = self._build_abnormal_scan_name_cache()

        def _resolve_scan_name(symbol: str) -> str:
            return name_cache.get(symbol, symbol[2:])

        events, dates, total_scanned, elapsed, scan_stats = scan_abnormal_movement(
            tdx_root=self._config.tdx_data_path,
            resolve_name=_resolve_scan_name,
            market_data_source=self._config.market_data_source,
            akshare_cache_dir=self._config.akshare_cache_dir,
            date_from=date_from,
            date_to=date_to,
            board_filters=list(payload.board_filters or []),
            include_warnings=bool(payload.include_warnings),
            scan_mode=str(payload.scan_mode or "snapshot"),
            parallel_workers=int(payload.parallel_workers or 0),
            cooling_days=int(getattr(payload, "cooling_days", 3) or 3),
        )
        return {
            "date_from": date_from,
            "date_to": date_to,
            "trigger_threshold_10": TRIGGER_THRESHOLD_10,
            "trigger_threshold_30": TRIGGER_THRESHOLD_30,
            "warn_threshold_10": WARN_THRESHOLD_10,
            "warn_threshold_30": WARN_THRESHOLD_30,
            "total_scanned": total_scanned,
            "dates": dates,
            "events": [
                {
                    "symbol": row.symbol,
                    "name": row.name,
                    "board": row.board,
                    "kind": row.kind,
                    "status": row.status,
                    "entry_date": row.entry_date,
                    "trigger_date": row.trigger_date,
                    "as_of_date": row.as_of_date,
                    "cum_deviation": round(row.cum_deviation, 2),
                    "episode_day": int(row.episode_day or 0),
                    "threshold": row.threshold,
                    "warn_threshold": row.warn_threshold,
                    "benchmark_symbol": row.benchmark_symbol,
                    "benchmark_name": row.benchmark_name,
                    "daily_limits": [
                        {
                            "date": item.date,
                            "day_offset": item.day_offset,
                            "cum_dev_10": round(item.cum_dev_10, 2) if item.cum_dev_10 is not None else None,
                            "cum_dev_30": round(item.cum_dev_30, 2) if item.cum_dev_30 is not None else None,
                            "max_dev_10": round(item.max_dev_10, 2) if item.max_dev_10 is not None else None,
                            "max_dev_30": round(item.max_dev_30, 2) if item.max_dev_30 is not None else None,
                            "max_stock_pct_10": round(item.max_stock_pct_10, 2) if item.max_stock_pct_10 is not None else None,
                            "max_stock_pct_30": round(item.max_stock_pct_30, 2) if item.max_stock_pct_30 is not None else None,
                            "effective_max_stock_pct": round(item.effective_max_stock_pct, 2)
                            if item.effective_max_stock_pct is not None
                            else None,
                            "actual_deviation": round(item.actual_deviation, 2) if item.actual_deviation is not None else None,
                            "actual_stock_pct": round(item.actual_stock_pct, 2) if item.actual_stock_pct is not None else None,
                            "actual_index_pct": round(item.actual_index_pct, 2) if item.actual_index_pct is not None else None,
                            "reset_10": item.reset_10,
                            "reset_30": item.reset_30,
                        }
                        for item in row.daily_limits
                    ],
                }
                for row in events
            ],
            "elapsed_sec": round(elapsed, 2),
            **scan_stats,
        }

    def scan_limit_up_ladder(self, *, request: Any) -> dict[str, Any]:
        from .core.market_momentum import scan_limit_up_timeline
        from .models import LimitUpLadderRequest

        payload = request if isinstance(request, LimitUpLadderRequest) else LimitUpLadderRequest.model_validate(request)
        date_to = str(payload.date_to or self._now_date()).strip()
        if payload.date_from:
            date_from = str(payload.date_from).strip()
        else:
            sample_candles = self._ensure_candles("sh600519")
            sample_dates = [item.time for item in sample_candles if item.time <= date_to]
            date_from = sample_dates[-750] if len(sample_dates) >= 750 else (sample_dates[0] if sample_dates else date_to)

        timeline, summaries, dates, total_scanned, elapsed = scan_limit_up_timeline(
            tdx_root=self._config.tdx_data_path,
            resolve_name=lambda symbol: self._resolve_symbol_name(symbol),
            market_data_source=self._config.market_data_source,
            akshare_cache_dir=self._config.akshare_cache_dir,
            date_from=date_from,
            date_to=date_to,
            recent_days=int(payload.recent_days),
            historical_min_boards=int(payload.historical_min_boards),
            board_filters=list(payload.board_filters or []),
        )
        return {
            "date_from": date_from,
            "date_to": date_to,
            "recent_days": int(payload.recent_days),
            "historical_min_boards": int(payload.historical_min_boards),
            "total_scanned": total_scanned,
            "dates": dates,
            "timeline": [
                {
                    "symbol": row.symbol,
                    "name": row.name,
                    "date": row.date,
                    "board_height": row.board_height,
                }
                for row in timeline
            ],
            "stocks": [
                {
                    "symbol": row.symbol,
                    "name": row.name,
                    "max_board_height": row.max_board_height,
                    "active_days": row.active_days,
                    "latest_board_height": row.latest_board_height,
                }
                for row in summaries
            ],
            "elapsed_sec": round(elapsed, 2),
        }

    def scan_sector_capital_flow(self, *, request: Any) -> dict[str, Any]:
        from .core.sector_capital_flow import scan_sector_capital_flow
        from .models import SectorCapitalFlowRequest

        payload = (
            request
            if isinstance(request, SectorCapitalFlowRequest)
            else SectorCapitalFlowRequest.model_validate(request)
        )
        date_to = str(payload.date_to or self._now_date()).strip()
        if payload.date_from:
            date_from = str(payload.date_from).strip()
        else:
            sample_candles = self._ensure_candles("sh600519")
            sample_dates = [item.time for item in sample_candles if item.time <= date_to]
            date_from = sample_dates[-750] if len(sample_dates) >= 750 else (sample_dates[0] if sample_dates else date_to)

        rows, series_rows, leaders, dates, elapsed = scan_sector_capital_flow(
            tdx_data_path=self._config.tdx_data_path,
            date_from=date_from,
            date_to=date_to,
            daily_top_n=int(payload.daily_top_n),
            flow_window=int(payload.flow_window),
        )
        return {
            "date_from": date_from,
            "date_to": date_to,
            "daily_top_n": int(payload.daily_top_n),
            "flow_window": int(payload.flow_window),
            "dates": dates,
            "flow_table": [
                {
                    "date": row.date,
                    "sector": row.sector,
                    "return_pct": row.return_pct,
                    "flow_score": row.flow_score,
                    "amount": row.amount,
                    "amount_delta": row.amount_delta,
                    "rank_return": row.rank_return,
                    "rank_flow": row.rank_flow,
                }
                for row in rows
            ],
            "series": [
                {
                    "sector": row.sector,
                    "points": [
                        {"date": point.date, "return_pct": point.return_pct, "flow_score": point.flow_score}
                        for point in row.points
                    ],
                }
                for row in series_rows
            ],
            "leaders": [
                {
                    "sector": row.sector,
                    "leader_days": row.leader_days,
                    "max_return_pct": row.max_return_pct,
                    "avg_flow_score": row.avg_flow_score,
                    "first_leader_date": row.first_leader_date,
                    "last_leader_date": row.last_leader_date,
                }
                for row in leaders
            ],
            "elapsed_sec": round(elapsed, 2),
        }

    def get_sentiment_valuation_quote(self, symbol: str) -> SentimentValuationQuoteResponse:
        normalized = str(symbol).strip().lower()
        degraded = False
        degraded_reason: str | None = None
        quote: dict[str, float | str | None] = {
            "name": None,
            "industry": None,
            "price": None,
            "market_cap_yi": None,
            "pe_ttm": None,
            "implied_earnings_yi": None,
        }

        secid = symbol_to_secid(normalized)
        if secid is not None:
            try:
                with httpx.Client(timeout=6.0, follow_redirects=True) as client:
                    resp = client.get(
                        "https://push2.eastmoney.com/api/qt/stock/get",
                        params={
                            "secid": secid,
                            "fields": "f43,f58,f116,f127,f162,f163,f164",
                        },
                        headers={
                            "User-Agent": (
                                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
                            )
                        },
                    )
                resp.raise_for_status()
                payload = resp.json()
                data = payload.get("data", {}) if isinstance(payload, dict) else {}
                if isinstance(data, dict):
                    quote = parse_eastmoney_quote(data)
            except Exception:
                degraded = True
                degraded_reason = "QUOTE_FETCH_FAILED"
        else:
            degraded = True
            degraded_reason = "SYMBOL_INVALID"

        name = str(quote.get("name") or "").strip()
        industry = str(quote.get("industry") or "").strip()
        if not name:
            profile = self._fetch_quote_profile(normalized)
            name = profile.get("name", "")
            if not industry:
                industry = profile.get("industry", "")

        suggested_pe, pe_tier, pe_label = suggest_base_pe(industry)
        index_symbol = "sh000001"
        index_close: float | None = None
        try:
            index_candles = self._ensure_candles(index_symbol)
            if index_candles:
                index_close = float(index_candles[-1].close)
        except Exception:
            pass
        index_coef = index_points_to_coef(index_close or 3000.0)

        activity = {"limit_up_count": 0, "big_gain_days": 0}
        try:
            stock_candles = self._ensure_candles(normalized)
            activity = count_limit_ups(stock_candles, normalized, name=name, window=60)
        except Exception:
            pass

        limit_up_count = int(activity.get("limit_up_count", 0))
        big_gain_days = int(activity.get("big_gain_days", 0))
        suggested_sentiment = suggest_sentiment_from_activity(limit_up_count, big_gain_days)

        market_cap_yi = quote.get("market_cap_yi")
        implied_earnings_yi = quote.get("implied_earnings_yi")
        implied_sentiment: float | None = None
        if isinstance(market_cap_yi, (int, float)) and isinstance(implied_earnings_yi, (int, float)):
            implied_sentiment = calc_implied_sentiment(
                float(market_cap_yi),
                earnings_yi=float(implied_earnings_yi),
                growth_coef=1.0,
                base_pe=float(suggested_pe),
                index_coef=float(index_coef),
            )

        return SentimentValuationQuoteResponse(
            symbol=normalized,
            name=name,
            industry=industry,
            price=float(quote["price"]) if isinstance(quote.get("price"), (int, float)) else None,
            market_cap_yi=float(market_cap_yi) if isinstance(market_cap_yi, (int, float)) else None,
            pe_ttm=float(quote["pe_ttm"]) if isinstance(quote.get("pe_ttm"), (int, float)) else None,
            implied_earnings_yi=float(implied_earnings_yi) if isinstance(implied_earnings_yi, (int, float)) else None,
            suggested_pe=float(suggested_pe),
            suggested_pe_tier=pe_tier,
            suggested_pe_label=pe_label,
            index_symbol=index_symbol,
            index_close=index_close,
            index_coef=round(float(index_coef), 4),
            limit_up_count_60d=limit_up_count,
            big_gain_days_60d=big_gain_days,
            suggested_sentiment_coef=suggested_sentiment,
            implied_sentiment_coef=round(implied_sentiment, 4) if implied_sentiment is not None else None,
            degraded=degraded,
            degraded_reason=degraded_reason,
        )

    def get_candles_payload(self, symbol: str) -> dict[str, object]:
        candles = self._ensure_candles(symbol)
        degraded = any(point.price_source == "approx" for point in candles)
        return {
            "symbol": symbol,
            "candles": candles,
            "degraded": degraded,
            "degraded_reason": "MINUTE_DATA_MISSING_PARTIAL" if degraded else None,
        }

    def get_intraday_payload(self, symbol: str, date: str) -> IntradayPayload:
        real_points, real_date = load_intraday_for_symbol_date(
            tdx_root=self._config.tdx_data_path,
            symbol=symbol,
            target_date=date,
        )
        if real_points:
            return IntradayPayload(
                symbol=symbol,
                date=real_date or date or self._now_date(),
                points=real_points,
                degraded=False,
                degraded_reason=None,
            )

        candles = self._ensure_candles(symbol)
        fallback_date = candles[-1].time if candles else self._now_date()
        matched = next((item for item in candles if item.time == date), None)
        target_date = date if matched else fallback_date
        base_price = matched.close if matched else (candles[-1].close if candles else 20.0)
        points = self._gen_intraday_points(symbol, target_date, base_price)
        degraded = True

        return IntradayPayload(
            symbol=symbol,
            date=target_date,
            points=points,
            degraded=degraded,
            degraded_reason="LC1_DATA_NOT_FOUND_FALLBACK_APPROX",
        )

    def _build_stock_signal_snapshot(self, symbol: str) -> SignalResult | None:
        normalized_symbol = str(symbol).strip().lower()
        if not normalized_symbol:
            return None

        try:
            candles = self._ensure_candles(normalized_symbol)
            chart_window_days = max(20, len(candles))
            row = self._build_row_from_candles(normalized_symbol) or self._latest_rows.get(normalized_symbol)
            if row is None:
                return None
            snapshot = self._calc_wyckoff_snapshot(row, window_days=chart_window_days)
        except Exception:
            return None

        events = snapshot["events"] if isinstance(snapshot.get("events"), list) else []
        risk_events = snapshot["risk_events"] if isinstance(snapshot.get("risk_events"), list) else []

        raw_event_dates = snapshot.get("event_dates")
        event_dates: dict[str, str] = {}
        if isinstance(raw_event_dates, dict):
            for event_code, event_date in raw_event_dates.items():
                code_text = str(event_code).strip()
                date_text = str(event_date).strip()
                if code_text and date_text:
                    event_dates[code_text] = date_text

        raw_event_chain = snapshot.get("event_chain")
        event_chain: list[dict[str, str]] = []
        if isinstance(raw_event_chain, list):
            for node in raw_event_chain:
                if not isinstance(node, dict):
                    continue
                code_text = str(node.get("event", "")).strip()
                date_text = str(node.get("date", "")).strip()
                category_text = str(node.get("category", "")).strip()
                if code_text and date_text:
                    event_chain.append({
                        "event": code_text,
                        "date": date_text,
                        "category": category_text or "other",
                    })
        if not event_chain and event_dates:
            risk_event_set = {str(event).strip() for event in risk_events}
            for code_text, date_text in sorted(event_dates.items(), key=lambda item: (item[1], item[0])):
                event_chain.append({
                    "event": code_text,
                    "date": date_text,
                    "category": "distributionRisk" if code_text in risk_event_set else "accumulation",
                })

        sequence_ok = bool(snapshot.get("sequence_ok"))
        entry_quality_score = float(snapshot.get("entry_quality_score", 0.0) or 0.0)
        event_score = float(snapshot.get("event_score", snapshot.get("event_strength_score", 0.0)) or 0.0)
        event_grade = self._normalize_event_grade(snapshot.get("event_grade", "C"))

        raw_event_confirmation_map = snapshot.get("event_confirmation_map")
        event_confirmation_map: dict[str, str] = {}
        if isinstance(raw_event_confirmation_map, dict):
            for event_name, status in raw_event_confirmation_map.items():
                name_text = str(event_name).strip()
                status_text = str(status).strip().lower()
                if not name_text or status_text not in {"confirmed", "pending", "failed"}:
                    continue
                event_confirmation_map[name_text] = status_text

        total_event_count = len(event_chain) if event_chain else len(events) + len(risk_events)
        signal_tags: list[str] = []
        if entry_quality_score >= 82:
            signal_tags.append("B")
        elif entry_quality_score >= 68:
            signal_tags.append("A")
        else:
            signal_tags.append("C")
        if risk_events:
            signal_tags.append("C")
        phase_text = str(snapshot.get("phase", "阶段未明"))
        if phase_text.startswith("吸筹D") or phase_text.startswith("吸筹E"):
            signal_tags.append("B")
        primary, secondary = self._resolve_signal_priority(signal_tags)

        trigger_date = str(snapshot.get("trigger_date") or "").strip()
        try:
            trigger_dt = datetime.strptime(trigger_date, "%Y-%m-%d")
        except ValueError:
            trigger_dt = datetime.now()
            trigger_date = trigger_dt.strftime("%Y-%m-%d")
        signal_age_days, _ = self._compute_signal_age_days(
            symbol=row.symbol,
            trigger_date=trigger_date,
            as_of_date=None,
        )
        expire_date = (trigger_dt + timedelta(days=2)).strftime("%Y-%m-%d")
        wyckoff_signal = str(snapshot.get("signal", "")).strip()
        phase_hint = str(snapshot.get("phase_hint", "")).strip()
        reason = f"{phase_hint} 关键事件={wyckoff_signal or '无'}"
        if risk_events:
            reason = f"{reason} 风险={','.join(str(event) for event in risk_events)}"

        raw_event_grade_map = snapshot.get("event_grade_map")
        event_grade_map: dict[str, str] = {}
        if isinstance(raw_event_grade_map, dict):
            for event_name, grade in raw_event_grade_map.items():
                name_text = str(event_name).strip()
                grade_text = str(grade).strip().upper()
                if not name_text or grade_text not in {"A", "B", "C"}:
                    continue
                event_grade_map[name_text] = grade_text

        return SignalResult(
            symbol=row.symbol,
            name=row.name,
            primary_signal=primary,  # type: ignore[arg-type]
            secondary_signals=secondary,  # type: ignore[arg-type]
            trigger_date=trigger_date,
            expire_date=expire_date,
            signal_age_days=signal_age_days,
            trigger_reason=reason,
            priority=3 if primary == "B" else 2 if primary == "A" else 1,
            wyckoff_phase=phase_text,
            wyckoff_signal=wyckoff_signal,
            structure_hhh=str(snapshot.get("structure_hhh", "-")),
            wy_event_count=total_event_count,
            wy_sequence_ok=sequence_ok,
            entry_quality_score=entry_quality_score,
            wy_events=[str(event) for event in events],
            wy_risk_events=[str(event) for event in risk_events],
            wy_event_dates=event_dates,
            wy_event_chain=event_chain,
            phase_hint=phase_hint,
            scan_mode="trend_pool",
            event_strength_score=float(snapshot.get("event_strength_score", 0.0) or 0.0),
            phase_score=float(snapshot.get("phase_score", 0.0) or 0.0),
            structure_score=float(snapshot.get("structure_score", 0.0) or 0.0),
            trend_score=float(snapshot.get("trend_score", 0.0) or 0.0),
            volatility_score=float(snapshot.get("volatility_score", 0.0) or 0.0),
            health_score=float(snapshot.get("health_score", 0.0) or 0.0),
            slope_stability=float(snapshot.get("slope_stability", 0.0) or 0.0),
            volatility_stability=float(snapshot.get("volatility_stability", 0.0) or 0.0),
            pullback_quality=float(snapshot.get("pullback_quality", 0.0) or 0.0),
            event_score=event_score,
            event_grade=event_grade,  # type: ignore[arg-type]
            event_background_score=float(snapshot.get("event_background_score", 0.0) or 0.0),
            event_position_score=float(snapshot.get("event_position_score", 0.0) or 0.0),
            event_vol_price_score=float(snapshot.get("event_vol_price_score", 0.0) or 0.0),
            event_confirmation_score=float(snapshot.get("event_confirmation_score", 0.0) or 0.0),
            candle_quality_score=float(snapshot.get("candle_quality_score", 0.0) or 0.0),
            cost_center_shift_score=float(snapshot.get("cost_center_shift_score", 0.0) or 0.0),
            weekly_context_score=float(snapshot.get("weekly_context_score", 0.0) or 0.0),
            weekly_context_multiplier=float(snapshot.get("weekly_context_multiplier", 1.0) or 1.0),
            event_recency_score=float(snapshot.get("event_recency_score", 0.0) or 0.0),
            phase_context_score=float(snapshot.get("phase_context_score", 0.0) or 0.0),
            risk_score=float(snapshot.get("risk_score", 0.0) or 0.0),
            confirmation_status=self._normalize_confirmation_status(snapshot.get("confirmation_status", "unconfirmed")),  # type: ignore[arg-type]
            event_confirmation_map=event_confirmation_map,
            event_grade_map=event_grade_map,
        )

    def get_analysis(self, symbol: str) -> StockAnalysisResponse:
        cached = self._latest_rows.get(symbol)
        signal = self._build_stock_signal_snapshot(symbol)
        if cached:
            analysis = StockAnalysis(
                symbol=symbol,
                suggest_start_date=self._days_ago(53),
                suggest_stage=cached.stage,
                suggest_trend_class=cached.trend_class,
                confidence=max(0.5, min(0.95, cached.ai_confidence)),
                reason=f"基于近端K线与量能自动识别，综合分 {cached.score}。",
                theme_stage=cached.theme_stage,
                degraded=cached.degraded,
                degraded_reason=cached.degraded_reason,
            )
            return StockAnalysisResponse(
                analysis=analysis,
                annotation=self._annotation_store.get(symbol),
                signal=signal,
            )

        base = next((stock for stock in STOCK_POOL if stock["symbol"] == symbol), None)
        analysis = StockAnalysis(
            symbol=symbol,
            suggest_start_date=self._days_ago(53),
            suggest_stage=(base["stage"] if base else "Mid"),  # type: ignore[arg-type]
            suggest_trend_class=(base["trend"] if base else "Unknown"),  # type: ignore[arg-type]
            confidence=0.74,
            reason="均线结构稳定，回调量能可控，板块热度仍在发酵。",
            theme_stage="发酵中",
            degraded=symbol == "sz002230",
            degraded_reason="AI_TIMEOUT_CACHE_FALLBACK" if symbol == "sz002230" else None,
        )
        return StockAnalysisResponse(
            analysis=analysis,
            annotation=self._annotation_store.get(symbol),
            signal=signal,
        )

    @state_locked
    def save_annotation(self, annotation: StockAnnotation) -> StockAnnotation:
        self._annotation_store[annotation.symbol] = annotation
        self._persist_app_state()
        return annotation

    @staticmethod
    def _resolve_signal_priority(signals: list[str]) -> tuple[str, list[str]]:
        order = {"B": 3, "A": 2, "C": 1}
        unique = [s for s in ["B", "A", "C"] if s in signals]
        if not unique:
            return "C", []
        primary = unique[0]
        secondary = [s for s in unique[1:] if order[s] < order[primary]]
        return primary, secondary

    @staticmethod
    def _phase_hint(phase: str) -> str:
        mapping = {
            "吸筹A": "疑似筹码吸收初期，重点观察抛压衰减。",
            "吸筹B": "震荡测试阶段，关注ST/TSO后的承接力度。",
            "吸筹C": "Spring 触发区，需确认假跌破后的快速收复。",
            "吸筹D": "SOS/JOC 强势确认，等待回踩与量能配合。",
            "吸筹E": "LPS 附近，偏向趋势延续但需防止高位背离。",
            "派发A": "出现派发迹象，建议降低仓位并谨慎追高。",
            "派发B": "派发风险增强，优先规避或仅做观察。",
            "派发C": "派发链路较完整，建议回避买入信号。",
            "派发D": "中后段弱势结构，关注反弹失败风险。",
            "派发E": "弱势延续概率高，等待新结构形成后再评估。",
        }
        return mapping.get(phase, "阶段未明，建议结合结构与量能进一步确认。")


    def _latest_run_id(self) -> str | None:
        if not self._run_store:
            return None
        latest = max(self._run_store.values(), key=lambda run: run.created_at)
        return latest.run_id

    @staticmethod
    def _merge_degraded_reasons(*reasons: str | None) -> str | None:
        merged: list[str] = []
        seen: set[str] = set()
        for raw in reasons:
            text = str(raw or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            merged.append(text)
        if not merged:
            return None
        return ";".join(merged)

    def _resolve_trend_pool_run_candidates(
        self,
        *,
        run: ScreenerRunDetail,
        trend_step: TrendPoolStep,
        as_of_date: str | None,
    ) -> tuple[list[ScreenerResult], str | None, str | None]:
        normalized_as_of_date = str(as_of_date or "").strip() or None
        if (normalized_as_of_date is None) or normalized_as_of_date == run.as_of_date:
            if trend_step == "step4":
                source = run.step_pools.step4
            elif trend_step == "step3":
                source = run.step_pools.step3
            elif trend_step == "step2":
                source = run.step_pools.step2
            elif trend_step == "step1":
                source = run.step_pools.step1
            else:
                source = run.step_pools.step4 or run.step_pools.step3
            degraded_reason = run.degraded_reason if run.degraded else None
            return source, degraded_reason, (run.as_of_date or normalized_as_of_date)

        input_rows, load_error, _cache_hit = self._load_input_pool_rows(
            markets=run.params.markets,
            return_window_days=run.params.return_window_days,
            as_of_date=normalized_as_of_date,
        )
        if not input_rows:
            return [], self._merge_degraded_reasons(load_error, run.degraded_reason if run.degraded else None), normalized_as_of_date

        step_configs = run.step_configs if isinstance(run.step_configs, ScreenerStepConfigs) else self._resolve_screener_step_configs(run.params)
        step1_pool, step2_pool, step3_pool, step4_pool = self._run_screener_filters_for_backtest(
            input_rows,
            mode=run.params.mode,
            step_configs=step_configs,
        )
        source = self._select_step_source_for_backtest(
            trend_step=trend_step,
            step1_pool=step1_pool,
            step2_pool=step2_pool,
            step3_pool=step3_pool,
            step4_pool=step4_pool,
        )
        degraded_reason = self._merge_degraded_reasons(load_error, run.degraded_reason if run.degraded else None)
        return source, degraded_reason, normalized_as_of_date

    def _resolve_signal_candidates(
        self,
        *,
        mode: SignalScanMode,
        run_id: str | None,
        trend_step: TrendPoolStep = "auto",
        as_of_date: str | None = None,
    ) -> tuple[list[ScreenerResult], str | None, str | None, str | None]:
        if mode == "trend_pool":
            resolved_run_id = str(run_id or "").strip() or None
            if not resolved_run_id:
                return [], "TREND_POOL_RUN_NOT_FOUND", None, as_of_date
            run = self._run_store.get(resolved_run_id)
            if run is None:
                return [], "TREND_POOL_RUN_NOT_FOUND", resolved_run_id, as_of_date
            source, degraded_reason, resolved_as_of_date = self._resolve_trend_pool_run_candidates(
                run=run,
                trend_step=trend_step,
                as_of_date=as_of_date,
            )
            if not source:
                if trend_step == "auto":
                    reason = "TREND_POOL_EMPTY"
                else:
                    reason = f"TREND_POOL_{trend_step.upper()}_EMPTY"
                return [], reason, resolved_run_id, resolved_as_of_date
            return source, degraded_reason, resolved_run_id, resolved_as_of_date

        source, error, cache_hit = self._load_input_pool_rows(
            markets=self._config.markets,
            return_window_days=self._config.return_window_days,
            as_of_date=as_of_date,
        )
        if not source:
            fallback_rows: list[ScreenerResult] = []
            for stock in STOCK_POOL:
                row = self._build_row_from_candles(stock["symbol"], as_of_date=as_of_date)
                if row:
                    fallback_rows.append(row)
            if fallback_rows:
                return fallback_rows, error or "FULL_MARKET_TDX_UNAVAILABLE_FALLBACK_CANDLES", None, as_of_date
            return [], error or "FULL_MARKET_SCAN_EMPTY", None, as_of_date

        ordered_rows = sorted(source, key=lambda row: str(row.symbol).strip().lower())
        deduped_rows: list[ScreenerResult] = []
        seen_symbols: set[str] = set()
        for row in ordered_rows:
            symbol = str(row.symbol).strip().lower()
            if not symbol or symbol in seen_symbols:
                continue
            seen_symbols.add(symbol)
            deduped_rows.append(row)

        protect_limit = max(1000, int(self._FULL_MARKET_SYSTEM_PROTECT_LIMIT))
        protect_hit = len(deduped_rows) > protect_limit
        if protect_hit:
            deduped_rows = deduped_rows[:protect_limit]

        reasons: list[str] = []
        if error:
            reasons.append(str(error))
        if cache_hit:
            reasons.append("INPUT_POOL_CACHE_HIT")
        if protect_hit:
            reasons.append("FULL_MARKET_SYSTEM_LIMIT_HIT")
        degraded_reason = ";".join(reasons) if reasons else None
        return deduped_rows, degraded_reason, None, as_of_date

    def _build_signal_replay_candidates(
        self,
        *,
        replay_as_of_date: str,
        replay_date_from: str,
        replay_pool_roll_mode: str,
        replay_screener_params: ScreenerParams,
        replay_trend_step: TrendPoolStep,
        replay_max_symbols: int,
        board_filters: list[BoardFilter],
    ) -> tuple[list[ScreenerResult], str | None]:
        """Build candidate pool using the same rolling logic as the backtest engine.

        For daily/weekly modes, this computes the correct refresh date for the
        given as_of_date and rebuilds the pool using that date's data.
        For position mode, it uses the initial date's pool carried forward.
        """
        effective_date_from = replay_date_from
        if effective_date_from > replay_as_of_date:
            effective_date_from = replay_as_of_date

        scan_dates = self._build_backtest_scan_dates(effective_date_from, replay_as_of_date)
        if not scan_dates:
            return [], None

        refresh_dates_used = self._resolve_backtest_refresh_dates(
            scan_dates=scan_dates,
            pool_roll_mode=replay_pool_roll_mode,
            refresh_dates=None,
        )
        if not refresh_dates_used:
            return [], None

        # Find the effective refresh date for the as_of_date:
        # it's the latest refresh date <= replay_as_of_date
        effective_refresh_date = refresh_dates_used[0]
        for rd in refresh_dates_used:
            if rd <= replay_as_of_date:
                effective_refresh_date = rd
            else:
                break

        resolved_step_configs = self._resolve_screener_step_configs(replay_screener_params)
        input_rows, load_error, _cache_hit = self._load_input_pool_rows(
            markets=replay_screener_params.markets,
            return_window_days=replay_screener_params.return_window_days,
            as_of_date=effective_refresh_date,
        )
        if not input_rows:
            return [], load_error

        step1_pool, step2_pool, step3_pool, step4_pool = self._run_screener_filters_for_backtest(
            input_rows,
            mode=replay_screener_params.mode,
            step_configs=resolved_step_configs,
        )
        source = self._select_step_source_for_backtest(
            trend_step=replay_trend_step,
            step1_pool=step1_pool,
            step2_pool=step2_pool,
            step3_pool=step3_pool,
            step4_pool=step4_pool,
        )
        if board_filters:
            source = [row for row in source if self._row_matches_board_filters(row, board_filters)]

        replay_candidates: list[ScreenerResult] = []
        seen: set[str] = set()
        for row in source:
            symbol_text = str(row.symbol).strip().lower()
            if not symbol_text or symbol_text in seen:
                continue
            seen.add(symbol_text)
            replay_candidates.append(row)
            if len(replay_candidates) >= replay_max_symbols:
                break

        return replay_candidates, load_error

    def _attach_ths_volume_signal(
        self,
        snapshot: dict[str, object] | None,
        candles: list[CandlePoint],
        symbol: str = "",
    ) -> dict[str, object]:
        base = dict(snapshot) if isinstance(snapshot, dict) else {}
        base["ths_main_retail_signal"] = calculate_ths_main_retail_signal(candles)
        base["force_rhythm_signal"] = calculate_force_rhythm_signal(candles)
        base["wulong_cluster_signal"] = calculate_wulong_cluster_signal(candles)
        base["b1_mtf_signal"] = calculate_b1_signal(candles)
        sector_data = self._resolve_sector_data(candles)
        base["trend_king_signal"] = calculate_trend_king_signal(candles, sector_data=sector_data)
        base["emotion_limit_up_signal"] = calculate_emotion_limit_up_signal(candles, sector_data=sector_data)
        base["limit_up_arb_signal"] = calculate_limit_up_arb_signal(
            candles,
            symbol=symbol,
            sector_data=sector_data,
        )
        return base

    def _resolve_sector_data(self, candles: list[CandlePoint]) -> dict[str, Any] | None:
        try:
            cache = self._sector_cache
            if cache.is_expired():
                tdx_path = str(getattr(self._config, "tdx_data_path", "") or "").strip()
                if tdx_path:
                    cache.refresh(tdx_path)
            if not cache.is_expired():
                closes = [float(c.close) for c in candles if float(c.close) > 0]
                sector_name, rank, m5 = cache.lookup(closes)
                if sector_name is not None:
                    return {"sector_name": sector_name, "sector_rank": rank, "sector_m5": m5}
        except Exception:
            pass
        return None

    @staticmethod
    def _build_ths_strategy_signal_context(
        strategy_id: str,
        snapshot: dict[str, object],
    ) -> dict[str, object] | None:
        normalized_id = str(strategy_id).strip()
        if normalized_id not in {"ths_main_force_flip_v1", "ths_main_force_golden_cross_v1"}:
            return None
        indicator = snapshot.get("ths_main_retail_signal")
        if not isinstance(indicator, dict):
            return None

        signal_name = "紫转黄"
        primary_signal = "A"
        if normalized_id == "ths_main_force_golden_cross_v1":
            signal_name = "金叉"
            primary_signal = "B"

        trigger_key = "purple_to_yellow" if normalized_id == "ths_main_force_flip_v1" else "golden_cross"
        if not bool(indicator.get(trigger_key, False)):
            return None

        trigger_date = str(indicator.get("trigger_date") or snapshot.get("trigger_date") or "").strip()
        if not trigger_date:
            return None

        main_force = float(indicator.get("main_force", 0.0) or 0.0)
        retail_force = float(indicator.get("retail_force", 0.0) or 0.0)
        main_force_power_score = max(0.0, min(100.0, float(indicator.get("main_force_power_score", 0.0) or 0.0)))
        force_gap_score = max(0.0, min(100.0, float(indicator.get("force_gap_score", 0.0) or 0.0)))
        retail_pressure_score = max(0.0, min(100.0, float(indicator.get("retail_pressure_score", 0.0) or 0.0)))
        explosion_ratio = max(0.0, float(indicator.get("main_force_explosion_ratio", 0.0) or 0.0))
        main_vs_retail_ratio = max(0.0, float(indicator.get("main_vs_retail_ratio", 0.0) or 0.0))
        signal_score = max(0.0, min(100.0, float(indicator.get("signal_score", 0.0) or 0.0)))
        prev_state = str(indicator.get("prev_main_force_state", "flat") or "flat")
        current_state = str(indicator.get("main_force_state", "flat") or "flat")

        if normalized_id == "ths_main_force_flip_v1":
            reason = (
                f"主力由紫转黄 前态={prev_state} 当前={current_state} "
                f"主力={main_force:.2f} 散户={retail_force:.2f} "
                f"爆发分={main_force_power_score:.1f} 对比分={force_gap_score:.1f}"
            )
        else:
            reason = (
                f"主力上穿散户(金叉) 主力={main_force:.2f} 散户={retail_force:.2f} "
                f"爆发分={main_force_power_score:.1f} 对比分={force_gap_score:.1f}"
            )

        event_grade = "C"
        if signal_score >= 80.0:
            event_grade = "A"
        elif signal_score >= 65.0:
            event_grade = "B"

        return {
            "trigger_date": trigger_date,
            "signal_name": signal_name,
            "primary_signal": primary_signal,
            "reason": reason,
            "phase": "主散量能",
            "phase_hint": (
                "按同花顺主力/散户量能公式重算"
                f"；主力爆发比={explosion_ratio:.2f} 主散比={main_vs_retail_ratio:.2f}"
            ),
            "entry_quality_score": signal_score,
            "health_score": main_force_power_score,
            "event_score": force_gap_score,
            "event_strength_score": signal_score,
            "phase_score": main_force_power_score,
            "structure_score": force_gap_score,
            "trend_score": signal_score,
            "volatility_score": max(0.0, min(100.0, 100.0 - retail_pressure_score)),
            "event_background_score": main_force_power_score,
            "event_position_score": force_gap_score,
            "event_confirmation_score": signal_score,
            "candle_quality_score": force_gap_score,
            "cost_center_shift_score": main_force_power_score,
            "event_grade": event_grade,
            "event_count": 1,
            "sequence_ok": True,
            "confirmation_status": "confirmed",
        }

    @staticmethod
    def _build_force_rhythm_strategy_signal_context(
        strategy_id: str,
        snapshot: dict[str, object],
        params: dict[str, object] | None = None,
    ) -> dict[str, object] | None:
        if str(strategy_id).strip() != "ths_force_rhythm_v1":
            return None
        indicator = snapshot.get("force_rhythm_signal")
        if not isinstance(indicator, dict):
            return None

        evaluation = evaluate_force_rhythm_signal(indicator, params if isinstance(params, dict) else None)
        if not bool(evaluation.get("signal", False)):
            return None

        trigger_date = str(evaluation.get("trigger_date") or snapshot.get("trigger_date") or "").strip()
        if not trigger_date:
            return None

        signal_score = max(0.0, min(100.0, float(evaluation.get("signal_score", 0.0) or 0.0)))
        rhythm_score = max(0.0, min(100.0, float(evaluation.get("rhythm_regularity_score", 0.0) or 0.0)))
        main_power_score = max(0.0, min(100.0, float(evaluation.get("main_force_power_score", 0.0) or 0.0)))
        cycle_count = max(0, int(evaluation.get("cycle_count", 0) or 0))
        cycle_cv = float(evaluation.get("cycle_cv", 0.0) or 0.0)
        trough_percentile = max(0.0, min(1.0, float(evaluation.get("trough_percentile", 0.0) or 0.0)))
        main_force = float(indicator.get("main_force", 0.0) or 0.0)
        main_force_state = str(indicator.get("main_force_state", "flat") or "flat")

        pattern_formed = bool(evaluation.get("rhythm_pattern_formed", indicator.get("rhythm_pattern_formed")))
        retail_sell = bool(evaluation.get("retail_sell_signal", indicator.get("retail_sell_signal")))
        retail_pct = max(0.0, min(1.0, float(indicator.get("retail_percentile", 0.0) or 0.0)))
        retail_pct = max(0.0, min(1.0, float(indicator.get("retail_percentile", 0.0) or 0.0)))

        reason = (
            f"主力节奏波谷买点 节奏波={'已形成' if pattern_formed else '未形成'} "
            f"主力={main_force:.2f} 状态={main_force_state} "
            f"周期={cycle_count} 主力分位={trough_percentile:.0%}"
        )
        if retail_sell:
            reason += "（散户卖出压力，已过滤）"
        event_grade = str(evaluation.get("event_grade") or "C")

        return {
            "trigger_date": trigger_date,
            "signal_name": "节奏波谷",
            "primary_signal": "R",
            "reason": reason,
            "phase": "主力节奏",
            "phase_hint": (
                f"周期CV={cycle_cv:.2f} 主力波谷分位={trough_percentile:.0%} "
                f"散户分位={retail_pct:.0%}（主力定节奏，散户仅作卖出参考）"
            ),
            "entry_quality_score": signal_score,
            "health_score": rhythm_score,
            "event_score": main_power_score,
            "event_strength_score": signal_score,
            "phase_score": rhythm_score,
            "structure_score": main_power_score,
            "trend_score": signal_score,
            "volatility_score": max(0.0, min(100.0, 100.0 - trough_percentile * 100.0)),
            "event_background_score": rhythm_score,
            "event_position_score": main_power_score,
            "event_confirmation_score": signal_score,
            "candle_quality_score": main_power_score,
            "cost_center_shift_score": rhythm_score,
            "event_grade": event_grade,
            "event_count": 1,
            "sequence_ok": True,
            "confirmation_status": "confirmed",
        }

    @staticmethod
    def _build_wulong_strategy_signal_context(
        strategy_id: str,
        snapshot: dict[str, object],
        params: dict[str, object] | None = None,
    ) -> dict[str, object] | None:
        if str(strategy_id).strip() != "wulong_cluster_v1":
            return None
        indicator = snapshot.get("wulong_cluster_signal")
        if not isinstance(indicator, dict):
            return None

        evaluation = evaluate_wulong_cluster_signal(indicator, params if isinstance(params, dict) else None)
        if not bool(evaluation.get("signal", False)):
            return None

        trigger_date = str(evaluation.get("trigger_date") or snapshot.get("trigger_date") or "").strip()
        if not trigger_date:
            return None

        signal_score = max(0.0, min(100.0, float(evaluation.get("signal_score", 0.0) or 0.0)))
        health_score = max(0.0, min(100.0, float(evaluation.get("health_score", signal_score) or signal_score)))
        event_score = max(0.0, min(100.0, float(evaluation.get("event_score", signal_score) or signal_score)))
        trend_score = max(0.0, min(100.0, float(evaluation.get("trend_score", signal_score) or signal_score)))
        structure_score = max(0.0, min(100.0, float(evaluation.get("structure_score", signal_score) or signal_score)))
        phase_score = max(0.0, min(100.0, float(evaluation.get("phase_score", signal_score) or signal_score)))
        volatility_score = max(0.0, min(100.0, float(evaluation.get("volatility_score", signal_score) or signal_score)))
        volume_ratio20 = max(0.0, float(evaluation.get("volume_ratio_20", 0.0) or 0.0))
        current_spread_pct = max(0.0, float(evaluation.get("current_spread_pct", 0.0) or 0.0))
        convergence_spread_pct = max(0.0, float(evaluation.get("convergence_spread_pct", 0.0) or 0.0))
        spread_expansion_multiple = max(0.0, float(evaluation.get("spread_expansion_multiple", 0.0) or 0.0))
        convergence_offset_days = max(0, int(evaluation.get("convergence_offset_days", 0) or 0))
        rising_ma_count = max(0, int(evaluation.get("rising_ma_count", 0) or 0))
        ma_values = evaluation.get("ma_values") if isinstance(evaluation.get("ma_values"), dict) else {}
        ma_text = (
            ", ".join(
                f"{key.upper()}={float(value):.2f}"
                for key, value in ma_values.items()
                if str(key).strip() and isinstance(value, (int, float))
            )
            or "MA5/10/20/30/60"
        )
        event_grade = str(evaluation.get("event_grade", "C") or "C").strip().upper()
        if event_grade not in {"A", "B", "C"}:
            event_grade = "C"

        return {
            "trigger_date": trigger_date,
            "signal_name": "五龙聚首",
            "primary_signal": "A",
            "reason": (
                "五龙聚首：5/10/20/30/60日均线由粘合转为多头发散，"
                f"放量倍数={volume_ratio20:.2f}，当前带宽={current_spread_pct * 100:.2f}% ，"
                f"粘合点距今 {convergence_offset_days} 天。"
            ),
            "phase": "均线共振",
            "phase_hint": (
                f"{ma_text}；粘合带宽={convergence_spread_pct * 100:.2f}% ，"
                f"发散倍数={spread_expansion_multiple:.2f}，上拐均线数={rising_ma_count}"
            ),
            "structure_hhh": "MA5|MA10|MA20|MA30|MA60",
            "entry_quality_score": signal_score,
            "health_score": health_score,
            "event_score": event_score,
            "event_strength_score": signal_score,
            "phase_score": phase_score,
            "structure_score": structure_score,
            "trend_score": trend_score,
            "volatility_score": volatility_score,
            "event_background_score": health_score,
            "event_position_score": structure_score,
            "event_vol_price_score": event_score,
            "event_confirmation_score": signal_score,
            "candle_quality_score": structure_score,
            "cost_center_shift_score": trend_score,
            "weekly_context_score": signal_score,
            "event_grade": event_grade,
            "event_count": 1,
            "sequence_ok": True,
            "confirmation_status": "confirmed",
        }

    @staticmethod
    def _build_b1_strategy_signal_context(
        strategy_id: str,
        snapshot: dict[str, object],
        params: dict[str, object] | None = None,
    ) -> dict[str, object] | None:
        if str(strategy_id).strip() != "b1_mtf_v1":
            return None
        indicator = snapshot.get("b1_mtf_signal")
        if not isinstance(indicator, dict):
            return None

        evaluation = evaluate_b1_signal(indicator, params if isinstance(params, dict) else None)
        if not bool(evaluation.get("signal", False)):
            return None

        trigger_date = str(indicator.get("trigger_date") or snapshot.get("trigger_date") or "").strip()
        if not trigger_date:
            return None

        signal_score = max(0.0, min(100.0, float(evaluation.get("signal_score", 0.0) or 0.0)))
        health_score = max(0.0, min(100.0, float(evaluation.get("health_score", signal_score) or signal_score)))
        event_score = max(0.0, min(100.0, float(evaluation.get("event_score", signal_score) or signal_score)))
        trend_score = max(0.0, min(100.0, float(evaluation.get("trend_score", signal_score) or signal_score)))
        structure_score = max(0.0, min(100.0, float(evaluation.get("structure_score", signal_score) or signal_score)))
        phase_score = max(0.0, min(100.0, float(evaluation.get("phase_score", signal_score) or signal_score)))
        volatility_score = max(0.0, min(100.0, float(evaluation.get("volatility_score", signal_score) or signal_score)))
        kdj_j = float(indicator.get("kdj_j", 0))
        volume_ratio = float(indicator.get("volume_ratio", 0))
        monthly_macd = float(indicator.get("monthly_macd", 0))
        weekly_macd = float(indicator.get("weekly_macd", 0))
        event_grade = str(evaluation.get("event_grade", "C") or "C").strip().upper()
        if event_grade not in {"A", "B", "C"}:
            event_grade = "C"

        return {
            "trigger_date": trigger_date,
            "signal_name": "B1多周期",
            "primary_signal": "A",
            "reason": (
                f"B1战法：月MACD多头(柱={monthly_macd:.4f}) + 周DIF>0(柱={weekly_macd:.4f}) + "
                f"日KDJ J={kdj_j:.1f}勾头 + 缩量比={volume_ratio:.4f}"
            ),
            "phase": "多周期共振",
            "phase_hint": (
                f"KDJ_J={kdj_j:.1f} 量比={volume_ratio:.2f} "
                f"月柱={monthly_macd:.4f} 周柱={weekly_macd:.4f}"
            ),
            "entry_quality_score": signal_score,
            "health_score": health_score,
            "event_score": event_score,
            "event_strength_score": signal_score,
            "phase_score": phase_score,
            "structure_score": structure_score,
            "trend_score": trend_score,
            "volatility_score": volatility_score,
            "event_background_score": health_score,
            "event_position_score": structure_score,
            "event_vol_price_score": event_score,
            "event_confirmation_score": signal_score,
            "candle_quality_score": structure_score,
            "cost_center_shift_score": trend_score,
            "weekly_context_score": signal_score,
            "event_grade": event_grade,
            "event_count": 1,
            "sequence_ok": True,
            "confirmation_status": "confirmed",
        }

    @staticmethod
    def _build_emotion_limit_up_strategy_signal_context(
        strategy_id: str,
        snapshot: dict[str, object],
        params: dict[str, object] | None = None,
    ) -> dict[str, object] | None:
        if str(strategy_id).strip() != "emotion_limit_up_v1":
            return None
        indicator = snapshot.get("emotion_limit_up_signal")
        if not isinstance(indicator, dict):
            return None

        evaluation = evaluate_emotion_limit_up_signal(indicator, params if isinstance(params, dict) else None)
        if not bool(evaluation.get("signal", False)):
            return None

        trigger_date = str(evaluation.get("trigger_date") or snapshot.get("trigger_date") or "").strip()
        if not trigger_date:
            return None

        signal_score = max(0.0, min(100.0, float(evaluation.get("signal_score", 0.0) or 0.0)))
        day_gain = max(0.0, float(indicator.get("day_gain", 0.0) or 0.0))
        force_ratio = max(0.0, float(indicator.get("force_ratio", 0.0) or 0.0))
        vol_ratio = max(0.0, float(indicator.get("vol_ratio", 0.0) or 0.0))
        signal_age = indicator.get("signal_age_days")
        label = str(evaluation.get("label") or "情绪涨停")
        event_grade = str(evaluation.get("event_grade", "C") or "C").strip().upper()
        if event_grade not in {"A", "B", "C"}:
            event_grade = "C"

        reason = (
            f"情绪涨停：{label} 日涨幅={day_gain:.2f}% "
            f"正/负比={force_ratio:.2f} 量比={vol_ratio:.2f}"
        )
        if signal_age is not None:
            reason += f" 信号距今={int(signal_age)}天"

        return {
            "trigger_date": trigger_date,
            "signal_name": "情绪涨停",
            "primary_signal": "J",
            "reason": reason,
            "phase": "涨停共振",
            "phase_hint": (
                f"天下无双信号+涨停共振；建议次日观察倍量与回撤后再尾盘买入。"
                f" 正/负={force_ratio:.2f} 信号年龄={signal_age if signal_age is not None else '-'}天"
            ),
            "entry_quality_score": signal_score,
            "health_score": signal_score,
            "event_score": signal_score,
            "event_strength_score": signal_score,
            "phase_score": signal_score,
            "structure_score": signal_score,
            "trend_score": signal_score,
            "volatility_score": max(0.0, min(100.0, 100.0 - day_gain)),
            "event_background_score": signal_score,
            "event_position_score": signal_score,
            "event_confirmation_score": signal_score,
            "candle_quality_score": signal_score,
            "cost_center_shift_score": signal_score,
            "event_grade": event_grade,
            "event_count": 1,
            "sequence_ok": True,
            "confirmation_status": "confirmed",
        }

    @staticmethod
    def _build_limit_up_arb_strategy_signal_context(
        strategy_id: str,
        snapshot: dict[str, object],
        params: dict[str, object] | None = None,
    ) -> dict[str, object] | None:
        if str(strategy_id).strip() != "limit_up_arb_v1":
            return None
        indicator = snapshot.get("limit_up_arb_signal")
        if not isinstance(indicator, dict):
            return None

        evaluation = evaluate_limit_up_arb_signal(indicator, params if isinstance(params, dict) else None)
        if not bool(evaluation.get("signal", False)):
            return None

        trigger_date = str(evaluation.get("trigger_date") or snapshot.get("trigger_date") or "").strip()
        if not trigger_date:
            return None

        signal_score = max(0.0, min(100.0, float(evaluation.get("signal_score", 0.0) or 0.0)))
        day_gain = max(0.0, float(indicator.get("day_gain", 0.0) or 0.0))
        volume_ratio_prev = max(0.0, float(indicator.get("volume_ratio_prev", 0.0) or 0.0))
        limit_up_date = str(indicator.get("limit_up_date") or "").strip()
        entry_price = float(indicator.get("entry_price", 0.0) or 0.0)
        event_grade = "A" if signal_score >= 75.0 else ("B" if signal_score >= 50.0 else "C")
        limit_up_text = f"涨停日={limit_up_date} " if limit_up_date else ""
        reason = (
            f"涨停套利：{trigger_date} 收盘已确认（{limit_up_text}"
            f"今涨幅={day_gain:.2f}% 相对昨量={volume_ratio_prev:.2f}x）；"
            f"入场参考价={entry_price:.2f}（触发日收盘价，与回测一致）；"
            f"止盈/止损/持仓天数请在回测页配置。"
        )

        return {
            "trigger_date": trigger_date,
            "signal_name": "涨停套利",
            "primary_signal": "K",
            "reason": reason,
            "phase": "涨停接力",
            "phase_hint": (
                f"收盘后确认信号，非盘中观察池；触发日={trigger_date}，"
                f"入场=触发日收盘价。今涨幅={day_gain:.2f}% 昨量比={volume_ratio_prev:.2f}x；"
                f"板块热度仅作排序参考。"
            ),
            "entry_quality_score": signal_score,
            "health_score": signal_score,
            "event_score": signal_score,
            "event_strength_score": signal_score,
            "phase_score": signal_score,
            "structure_score": signal_score,
            "trend_score": signal_score,
            "volatility_score": max(0.0, min(100.0, 100.0 - day_gain)),
            "event_background_score": signal_score,
            "event_position_score": signal_score,
            "event_confirmation_score": signal_score,
            "candle_quality_score": signal_score,
            "cost_center_shift_score": signal_score,
            "event_grade": event_grade,
            "event_count": 1,
            "sequence_ok": True,
            "confirmation_status": "confirmed",
        }

    def _build_strategy_signal_context(
        self,
        strategy_id: str,
        snapshot: dict[str, object],
        params: dict[str, object] | None = None,
    ) -> dict[str, object] | None:
        ths_context = self._build_ths_strategy_signal_context(strategy_id, snapshot)
        if ths_context is not None:
            return ths_context
        rhythm_context = self._build_force_rhythm_strategy_signal_context(strategy_id, snapshot, params)
        if rhythm_context is not None:
            return rhythm_context
        b1_context = self._build_b1_strategy_signal_context(strategy_id, snapshot, params)
        if b1_context is not None:
            return b1_context
        tk_context = self._build_trend_king_strategy_signal_context(strategy_id, snapshot, params)
        if tk_context is not None:
            return tk_context
        elu_context = self._build_emotion_limit_up_strategy_signal_context(strategy_id, snapshot, params)
        if elu_context is not None:
            return elu_context
        lua_context = self._build_limit_up_arb_strategy_signal_context(strategy_id, snapshot, params)
        if lua_context is not None:
            return lua_context
        return self._build_wulong_strategy_signal_context(strategy_id, snapshot, params)

    @staticmethod
    def _resolve_trend_king_mode(strategy_id: str, params: dict[str, object] | None) -> dict[str, object]:
        merged = dict(params) if isinstance(params, dict) else {}
        if "mode" not in merged or not str(merged["mode"]).strip():
            sid = str(strategy_id).strip()
            if "limitup" in sid:
                merged["mode"] = "a"
            elif "rally" in sid:
                merged["mode"] = "b"
            elif "pullback" in sid:
                merged["mode"] = "c"
            else:
                merged["mode"] = "all"
        return merged

    @staticmethod
    def _build_trend_king_strategy_signal_context(
        strategy_id: str,
        snapshot: dict[str, object],
        params: dict[str, object] | None = None,
    ) -> dict[str, object] | None:
        from .core.backtest_engine import TREND_KING_STRATEGY_IDS
        normalized_id = str(strategy_id).strip()
        if normalized_id not in TREND_KING_STRATEGY_IDS:
            return None
        indicator = snapshot.get("trend_king_signal")
        if not isinstance(indicator, dict):
            return None
        merged_params = InMemoryStore._resolve_trend_king_mode(strategy_id, params)
        evaluation = evaluate_trend_king_signal(indicator, merged_params)
        if not bool(evaluation.get("signal", False)):
            return None
        trigger_date = str(evaluation.get("trigger_date") or "").strip()
        if not trigger_date:
            return None

        signal_score = max(0.0, min(100.0, float(evaluation.get("signal_score", 0.0) or 0.0)))
        zt_score = max(0.0, min(100.0, float(evaluation.get("zt_score", 0.0) or 0.0)))
        cfm_score = max(0.0, min(100.0, float(evaluation.get("cfm_score", 0.0) or 0.0)))

        mode_label = "趋势为王"
        if normalized_id == "trend_king_limitup_v1" or evaluation.get("mode_a"):
            mode_label = "涨停精选"
        elif normalized_id == "trend_king_rally_v1" or evaluation.get("mode_b"):
            mode_label = "涨势确认"
        elif normalized_id == "trend_king_pullback_v1" or evaluation.get("mode_c"):
            mode_label = "涨停跟踪"

        primary_signal = "A"
        if evaluation.get("mode_b"):
            primary_signal = "B"
        elif evaluation.get("mode_c"):
            primary_signal = "C"

        event_grade = "A" if signal_score >= 80 else ("B" if signal_score >= 65 else "C")
        return {
            "signal_name": mode_label,
            "primary_signal": primary_signal,
            "trigger_date": trigger_date,
            "entry_quality_score": signal_score,
            "health_score": signal_score * 0.7 + zt_score * 0.3,
            "event_score": signal_score * 0.6 + cfm_score * 0.4,
            "event_strength_score": signal_score,
            "phase_score": zt_score,
            "structure_score": cfm_score,
            "trend_score": signal_score,
            "volatility_score": signal_score * 0.5,
            "event_background_score": zt_score,
            "event_position_score": cfm_score,
            "event_vol_price_score": signal_score,
            "event_confirmation_score": signal_score,
            "candle_quality_score": signal_score * 0.8,
            "cost_center_shift_score": signal_score * 0.6,
            "weekly_context_score": signal_score,
            "event_grade": event_grade,
            "event_count": 1,
            "sequence_ok": True,
            "confirmation_status": "confirmed",
        }

    def _calc_wyckoff_snapshot(
        self,
        row: ScreenerResult,
        window_days: int,
        *,
        as_of_date: str | None = None,
    ) -> dict[str, object]:
        """Calculate Wyckoff snapshot with lazy persisted daily event cache."""
        candles, resolved_as_of_date = self._slice_candles_as_of(
            self._ensure_candles(row.symbol), as_of_date
        )
        symbol = str(row.symbol).strip().lower()
        trade_date = str(resolved_as_of_date or "").strip()
        data_source = str(self._config.market_data_source).strip() or "unknown"
        runtime_binding = self._get_backtest_runtime_event_judgment_binding()
        if runtime_binding is not None and isinstance(runtime_binding.get("profile"), dict):
            event_judgment_profile = dict(runtime_binding["profile"])
            event_judgment_profile_hash = str(runtime_binding.get("profile_hash", "")).strip() or self._event_judgment_profile_hash(
                event_judgment_profile
            )
        else:
            event_judgment_profile = self._active_event_judgment_profile()
            event_judgment_profile_hash = self._active_event_judgment_profile_hash()
        params_hash = build_wyckoff_params_hash(
            window_days,
            profile_hash=event_judgment_profile_hash,
        )

        if symbol and trade_date:
            read_started = time.perf_counter()
            cached = self._wyckoff_event_store.get_snapshot(
                symbol=symbol,
                trade_date=trade_date,
                window_days=window_days,
                algo_version=self._wyckoff_event_algo_version,
                data_source=data_source,
                data_version=self._wyckoff_event_data_version,
                params_hash=params_hash,
            )
            read_duration_ms = (time.perf_counter() - read_started) * 1000.0
            self._record_wyckoff_snapshot_read_latency(read_duration_ms)
            if cached is not None:
                self._bump_wyckoff_metric("cache_hits", 1)
                return self._attach_ths_volume_signal(cached, candles, symbol=symbol)

        self._bump_wyckoff_metric("cache_misses", 1)
        snapshot = SignalAnalyzer.calculate_wyckoff_snapshot(
            row,
            candles,
            window_days,
            event_judgment_profile=event_judgment_profile,
        )
        snapshot = self._attach_ths_volume_signal(snapshot, candles, symbol=symbol)
        quality_flags = self._inspect_wyckoff_snapshot_quality(snapshot, trade_date=trade_date)
        self._record_wyckoff_snapshot_quality(quality_flags)
        if symbol and trade_date:
            write_ok = self._wyckoff_event_store.upsert_snapshot(
                symbol=symbol,
                trade_date=trade_date,
                window_days=window_days,
                algo_version=self._wyckoff_event_algo_version,
                data_source=data_source,
                data_version=self._wyckoff_event_data_version,
                params_hash=params_hash,
                snapshot=snapshot,
            )
            if write_ok:
                self._bump_wyckoff_metric("lazy_fill_writes", 1)
        return snapshot

    def _is_signals_disk_cache_enabled(self) -> bool:
        return self._env_flag("TDX_TREND_SIGNALS_DISK_CACHE", True)

    @staticmethod
    def _signals_disk_cache_ttl_sec() -> float:
        raw = os.getenv("TDX_TREND_SIGNALS_DISK_CACHE_TTL_SEC", "").strip()
        if not raw:
            return 6 * 3600.0
        try:
            return max(0.0, float(raw))
        except Exception:
            return 6 * 3600.0

    def _build_signals_disk_cache_key(self, core_cache_key: str) -> str:
        payload = {
            "version": self._SIGNALS_RESULT_CACHE_VERSION,
            "core_key": str(core_cache_key).strip(),
            "market_data_source": str(self._config.market_data_source).strip(),
            "candles_window_bars": int(self._config.candles_window_bars),
            "wyckoff_algo_version": str(self._wyckoff_event_algo_version).strip(),
            "wyckoff_data_version": str(self._wyckoff_event_data_version).strip(),
        }
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:24]

    def _signals_disk_cache_file(self, core_cache_key: str) -> Path:
        cache_key = self._build_signals_disk_cache_key(core_cache_key)
        return self._resolve_signals_cache_dir() / f"{cache_key}.json"

    @staticmethod
    def _signals_runtime_cache_ttl_sec() -> float:
        raw = os.getenv("TDX_TREND_SIGNALS_RUNTIME_CACHE_TTL_SEC", "").strip()
        if not raw:
            return 180.0
        try:
            return max(0.0, float(raw))
        except Exception:
            return 180.0

    @staticmethod
    def _signals_runtime_cache_max_items() -> int:
        raw = os.getenv("TDX_TREND_SIGNALS_RUNTIME_CACHE_MAX_ITEMS", "").strip()
        if not raw:
            return 16
        try:
            return max(1, int(raw))
        except Exception:
            return 16

    def _prune_signals_runtime_cache(self, *, now_ts: float | None = None) -> None:
        if not self._signals_cache:
            return
        current_ts = time.time() if now_ts is None else float(now_ts)
        ttl_sec = self._signals_runtime_cache_ttl_sec()
        if ttl_sec > 0:
            stale_keys = [
                key
                for key, (created_at, _payload) in self._signals_cache.items()
                if (current_ts - float(created_at)) > ttl_sec
            ]
            for key in stale_keys:
                self._signals_cache.pop(key, None)
        max_items = self._signals_runtime_cache_max_items()
        if len(self._signals_cache) > max_items:
            stale_items = sorted(
                self._signals_cache.items(),
                key=lambda item: float(item[1][0]),
            )
            overflow = len(self._signals_cache) - max_items
            for key, _value in stale_items[:overflow]:
                self._signals_cache.pop(key, None)

    def _load_signals_runtime_cache(self, cache_key: str, *, now_ts: float | None = None) -> SignalsResponse | None:
        current_ts = time.time() if now_ts is None else float(now_ts)
        self._prune_signals_runtime_cache(now_ts=current_ts)
        cached = self._signals_cache.get(cache_key)
        if cached is None:
            return None
        created_at, payload = cached
        ttl_sec = self._signals_runtime_cache_ttl_sec()
        if ttl_sec > 0 and (current_ts - float(created_at)) > ttl_sec:
            self._signals_cache.pop(cache_key, None)
            return None
        return payload

    def _save_signals_runtime_cache(
        self,
        cache_key: str,
        payload: SignalsResponse,
        *,
        now_ts: float | None = None,
    ) -> None:
        current_ts = time.time() if now_ts is None else float(now_ts)
        self._signals_cache[cache_key] = (current_ts, payload)
        self._prune_signals_runtime_cache(now_ts=current_ts)

    def _load_signals_disk_cache(self, core_cache_key: str) -> SignalsResponse | None:
        path = self._signals_disk_cache_file(core_cache_key)
        if not path.exists():
            return None
        ttl_sec = self._signals_disk_cache_ttl_sec()
        if ttl_sec > 0:
            try:
                age_sec = max(0.0, time.time() - path.stat().st_mtime)
                if age_sec > ttl_sec:
                    return None
            except Exception:
                return None
        try:
            payload_raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload_raw, dict):
                return None
            body = payload_raw.get("payload")
            if not isinstance(body, dict):
                return None
            return SignalsResponse(**body)
        except Exception:
            return None

    def _save_signals_disk_cache(self, core_cache_key: str, payload: SignalsResponse) -> bool:
        path = self._signals_disk_cache_file(core_cache_key)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            body = {
                "schema_version": 1,
                "created_at": self._now_datetime(),
                "payload": payload.model_dump(exclude_none=True),
            }
            tmp_path = path.with_suffix(".tmp")
            tmp_path.write_text(
                json.dumps(body, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            tmp_path.replace(path)
            return True
        except Exception:
            return False

    def _signals_cache_key(
        self,
        *,
        mode: SignalScanMode,
        strategy_id: str,
        strategy_version: str,
        strategy_params_hash: str,
        event_judgment_profile_hash: str,
        run_id: str | None,
        trend_step: TrendPoolStep,
        market_filters: list[Market],
        board_filters: list[BoardFilter],
        as_of_date: str | None,
        window_days: int,
        min_score: float,
        require_sequence: bool,
        min_event_count: int,
        signal_age_min: int,
        signal_age_max: int | None,
        backtest_date_from: str | None,
        backtest_pool_roll_mode: BacktestPoolRollMode | None,
        backtest_max_symbols: int | None,
    ) -> str:
        payload = {
            "mode": mode,
            "strategy_id": str(strategy_id).strip(),
            "strategy_version": str(strategy_version).strip(),
            "strategy_params_hash": str(strategy_params_hash).strip(),
            "event_judgment_profile_hash": str(event_judgment_profile_hash).strip(),
            "run_id": run_id or "",
            "trend_step": trend_step,
            "market_filters": market_filters,
            "board_filters": board_filters,
            "as_of_date": as_of_date or "",
            "window_days": window_days,
            "min_score": round(min_score, 3),
            "require_sequence": require_sequence,
            "min_event_count": min_event_count,
            "signal_age_min": int(signal_age_min),
            "signal_age_max": (int(signal_age_max) if signal_age_max is not None else None),
            "backtest_date_from": str(backtest_date_from or "").strip(),
            "backtest_pool_roll_mode": str(backtest_pool_roll_mode or "").strip(),
            "backtest_max_symbols": (int(backtest_max_symbols) if backtest_max_symbols is not None else None),
        }
        return json.dumps(payload, sort_keys=True, ensure_ascii=True)

    def get_signals(
        self,
        *,
        mode: SignalScanMode = "trend_pool",
        run_id: str | None = None,
        trend_step: TrendPoolStep = "auto",
        strategy_id: str | None = None,
        strategy_params: dict[str, Any] | None = None,
        market_filters: list[Market] | None = None,
        board_filters: list[BoardFilter] | None = None,
        as_of_date: str | None = None,
        refresh: bool = False,
        window_days: int = 60,
        min_score: float = 60,
        require_sequence: bool = False,
        min_event_count: int = 1,
        signal_age_min: int = 0,
        signal_age_max: int | None = None,
        backtest_date_from: str | None = None,
        backtest_pool_roll_mode: BacktestPoolRollMode | None = None,
        backtest_max_symbols: int | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> SignalsResponse:
        strategy_meta, normalized_strategy_params, strategy_params_hash = self._resolve_strategy_runtime(
            strategy_id=strategy_id,
            strategy_params=strategy_params,
        )
        strategy_signal_overrides = self._strategy_registry.resolve_signal_overrides(
            str(strategy_meta["strategy_id"]),
            normalized_strategy_params,
        )
        strategy_capabilities = strategy_meta.get("capabilities", {}) if isinstance(strategy_meta, dict) else {}
        strategy_supports_signal_age_filter = bool(strategy_capabilities.get("supports_signal_age_filter", True))
        capability_notes: list[str] = []
        if "min_score" in strategy_signal_overrides:
            min_score = float(strategy_signal_overrides["min_score"])
        if "min_event_count" in strategy_signal_overrides:
            min_event_count = int(strategy_signal_overrides["min_event_count"])
        if "require_sequence" in strategy_signal_overrides:
            require_sequence = bool(strategy_signal_overrides["require_sequence"])
        strategy_health_score_min = max(
            0.0,
            min(100.0, float(strategy_signal_overrides.get("health_score_min", 0.0) or 0.0)),
        )
        strategy_event_score_min = max(
            0.0,
            min(100.0, float(strategy_signal_overrides.get("event_score_min", 0.0) or 0.0)),
        )
        strategy_event_grade_min = self._normalize_event_grade(strategy_signal_overrides.get("event_grade_min", "C"))
        strategy_require_key_event_confirmation = bool(
            strategy_signal_overrides.get("require_key_event_confirmation", False)
        )

        replay_date_from = str(backtest_date_from or "").strip() or None
        replay_pool_roll_mode = (
            backtest_pool_roll_mode
            if backtest_pool_roll_mode in {"daily", "weekly", "position"}
            else None
        )
        replay_max_symbols = None
        if backtest_max_symbols is not None:
            try:
                replay_max_symbols = max(20, min(2000, int(backtest_max_symbols)))
            except Exception:
                replay_max_symbols = None

        requested_signal_age_min = max(0, int(signal_age_min))
        requested_signal_age_max = (
            max(0, int(signal_age_max))
            if signal_age_max is not None
            else None
        )
        if (
            requested_signal_age_max is not None
            and requested_signal_age_max < requested_signal_age_min
        ):
            requested_signal_age_min, requested_signal_age_max = (
                requested_signal_age_max,
                requested_signal_age_min,
            )
        normalized_signal_age_min = requested_signal_age_min
        normalized_signal_age_max = requested_signal_age_max
        if (not strategy_supports_signal_age_filter) and (
            requested_signal_age_min > 0 or requested_signal_age_max is not None
        ):
            normalized_signal_age_min = 0
            normalized_signal_age_max = None
            capability_notes.append(
                f"策略 {strategy_meta.get('strategy_id')} 不支持 signal_age 过滤，已忽略 signal_age_min/max。"
            )
        if str(strategy_meta.get("strategy_id", "")).strip() == "limit_up_arb_v1":
            capability_notes.append(
                "涨停套利仅输出收盘后已确认信号（昨涨停+今涨幅+今相对昨量同时满足），"
                "触发日=确认日，入场价=触发日收盘价，与回测入场一致；非「昨涨停待观察」观察池。"
            )
        allowed_markets = {"sh", "sz", "bj"}
        normalized_market_filters = list(dict.fromkeys(item for item in (market_filters or []) if item in allowed_markets))
        allowed_board_filters = {"main", "gem", "star", "beijing", "st"}
        normalized_board_filters = list(
            dict.fromkeys(item for item in (board_filters or []) if item in allowed_board_filters)
        )

        self._raise_if_cross_validate_cancelled(should_cancel)
        candidates, degraded_reason, resolved_run_id, resolved_as_of_date = self._resolve_signal_candidates(
            mode=mode,
            run_id=run_id,
            trend_step=trend_step,
            as_of_date=as_of_date,
        )
        self._raise_if_cross_validate_cancelled(should_cancel)

        if mode == "trend_pool" and replay_date_from and replay_pool_roll_mode in {"daily", "weekly", "position"}:
            replay_as_of_date = str(resolved_as_of_date or as_of_date or "").strip()
            if replay_as_of_date:
                try:
                    replay_run = self._require_backtest_trend_pool_run(run_id or resolved_run_id)
                    replay_step_configs = (
                        replay_run.step_configs
                        if isinstance(replay_run.step_configs, ScreenerStepConfigs)
                        else self._resolve_screener_step_configs(replay_run.params)
                    )
                    replay_screener_params = self._bind_step_configs_to_screener_params(
                        replay_run.params,
                        replay_step_configs,
                    )
                    replay_limit = (
                        int(replay_max_symbols)
                        if replay_max_symbols is not None
                        else max(20, min(2000, len(candidates) if len(candidates) > 0 else 120))
                    )
                    replay_candidates, replay_load_error = self._build_signal_replay_candidates(
                        replay_as_of_date=replay_as_of_date,
                        replay_date_from=replay_date_from,
                        replay_pool_roll_mode=replay_pool_roll_mode,
                        replay_screener_params=replay_screener_params,
                        replay_trend_step=trend_step,
                        replay_max_symbols=replay_limit,
                        board_filters=normalized_board_filters,
                    )
                    if replay_candidates or not candidates:
                        candidates = replay_candidates
                    roll_label = {"daily": "每日滚动", "weekly": "每周滚动", "position": "持仓触发滚动"}.get(
                        replay_pool_roll_mode, replay_pool_roll_mode
                    )
                    capability_notes.append(
                        f"回测复盘口径候选池: roll={roll_label}, as_of={replay_as_of_date}, "
                        f"date_from={replay_date_from}, symbols={len(candidates)}。"
                    )
                    if replay_load_error:
                        degraded_reason = self._merge_degraded_reasons(degraded_reason, replay_load_error)
                except Exception as exc:
                    capability_notes.append(f"回测复盘口径候选池构建失败，已回退按筛选日期重建池：{exc}")

        if normalized_market_filters:
            candidates = [row for row in candidates if self._row_matches_market_filters(row, normalized_market_filters)]
        if normalized_board_filters:
            candidates = [row for row in candidates if self._row_matches_board_filters(row, normalized_board_filters)]
        strategy_id_text = str(strategy_meta["strategy_id"])
        before_strategy_universe_count = len(candidates)
        if mode == "full_market":
            capability_notes.append(
                f"full_market 跳过策略预筛，直接逐票判定信号（strategy_id={strategy_id_text}）。"
            )
        candidates = (
            list(candidates)
            if mode == "full_market"
            else self._strategy_registry.build_universe(
                strategy_id=strategy_id_text,
                candidates=candidates,
                params=normalized_strategy_params,
                mode="signals",
            )
        )
        if mode != "full_market" and len(candidates) != before_strategy_universe_count:
            capability_notes.append(
                f"策略候选池过滤: {before_strategy_universe_count} -> {len(candidates)}（strategy_id={strategy_id_text}）。"
            )
        active_event_profile = self._active_event_judgment_profile()
        active_event_profile_id = str(active_event_profile.get("profile_id", "")).strip() or self._default_event_judgment_profile_id()
        active_event_profile_hash = self._active_event_judgment_profile_hash()
        capability_notes.append(f"事件判别模板: id={active_event_profile_id}, hash={active_event_profile_hash}")
        source_count = len(candidates)
        cache_key = self._signals_cache_key(
            mode=mode,
            strategy_id=str(strategy_meta["strategy_id"]),
            strategy_version=str(strategy_meta.get("version") or "1.0.0"),
            strategy_params_hash=strategy_params_hash,
            event_judgment_profile_hash=active_event_profile_hash,
            run_id=resolved_run_id if mode == "trend_pool" else run_id,
            trend_step=trend_step if mode == "trend_pool" else "auto",
            market_filters=normalized_market_filters,
            board_filters=normalized_board_filters,
            as_of_date=resolved_as_of_date,
            window_days=window_days,
            min_score=min_score,
            require_sequence=require_sequence,
            min_event_count=min_event_count,
            signal_age_min=normalized_signal_age_min,
            signal_age_max=normalized_signal_age_max,
            backtest_date_from=replay_date_from,
            backtest_pool_roll_mode=replay_pool_roll_mode,
            backtest_max_symbols=replay_max_symbols,
        )
        now_ts = time.time()
        cached_payload = None if refresh else self._load_signals_runtime_cache(cache_key, now_ts=now_ts)
        if cached_payload is not None:
            self._raise_if_cross_validate_cancelled(should_cancel)
            cached_notes = list(cached_payload.notes)
            for note in capability_notes:
                if note not in cached_notes:
                    cached_notes.insert(0, note)
            return cached_payload.model_copy(
                update={
                    "cache_hit": True,
                    "as_of_date": resolved_as_of_date or cached_payload.as_of_date,
                    "degraded": cached_payload.degraded or bool(degraded_reason),
                    "degraded_reason": degraded_reason or cached_payload.degraded_reason,
                    "source_count": source_count or cached_payload.source_count,
                    "strategy_id": str(strategy_meta["strategy_id"]),
                    "strategy_version": str(strategy_meta.get("version") or cached_payload.strategy_version),
                    "strategy_params": dict(normalized_strategy_params),
                    "strategy_params_hash": str(strategy_params_hash),
                    "notes": cached_notes,
                }
            )

        if (not refresh) and self._is_signals_disk_cache_enabled():
            cached_disk_payload = self._load_signals_disk_cache(cache_key)
            if cached_disk_payload is not None:
                self._raise_if_cross_validate_cancelled(should_cancel)
                self._save_signals_runtime_cache(cache_key, cached_disk_payload, now_ts=now_ts)
                cached_notes = list(cached_disk_payload.notes)
                for note in capability_notes:
                    if note not in cached_notes:
                        cached_notes.insert(0, note)
                return cached_disk_payload.model_copy(
                    update={
                        "cache_hit": True,
                        "as_of_date": resolved_as_of_date or cached_disk_payload.as_of_date,
                        "degraded": cached_disk_payload.degraded or bool(degraded_reason),
                        "degraded_reason": degraded_reason or cached_disk_payload.degraded_reason,
                        "source_count": source_count or cached_disk_payload.source_count,
                        "strategy_id": str(strategy_meta["strategy_id"]),
                        "strategy_version": str(strategy_meta.get("version") or cached_disk_payload.strategy_version),
                        "strategy_params": dict(normalized_strategy_params),
                        "strategy_params_hash": str(strategy_params_hash),
                        "notes": cached_notes,
                    }
                )

        items: list[SignalResult] = []
        seen_symbols: set[str] = set()
        resolved_signal_as_of_date = resolved_as_of_date
        row_by_symbol = {str(row.symbol).strip().lower(): row for row in candidates}

        for scan_index, row in enumerate(candidates):
            if scan_index % self._SIGNAL_SCAN_CANCEL_CHECK_BATCH == 0:
                self._raise_if_cross_validate_cancelled(should_cancel)
            if row.symbol in seen_symbols:
                continue
            snapshot = self._calc_wyckoff_snapshot(row, window_days=window_days, as_of_date=resolved_as_of_date)
            if not self._strategy_registry.generate_signals(
                strategy_id=strategy_id_text,
                row=row,
                snapshot=snapshot,
                params=normalized_strategy_params,
            ):
                continue
            events = snapshot["events"] if isinstance(snapshot["events"], list) else []
            risk_events = snapshot["risk_events"] if isinstance(snapshot["risk_events"], list) else []
            raw_event_dates = snapshot.get("event_dates")
            event_dates: dict[str, str] = {}
            if isinstance(raw_event_dates, dict):
                for event_code, event_date in raw_event_dates.items():
                    code_text = str(event_code).strip()
                    date_text = str(event_date).strip()
                    if code_text and date_text:
                        event_dates[code_text] = date_text
            raw_event_chain = snapshot.get("event_chain")
            event_chain: list[dict[str, str]] = []
            if isinstance(raw_event_chain, list):
                for node in raw_event_chain:
                    if not isinstance(node, dict):
                        continue
                    code_text = str(node.get("event", "")).strip()
                    date_text = str(node.get("date", "")).strip()
                    category_text = str(node.get("category", "")).strip()
                    if code_text and date_text:
                        event_chain.append({
                            "event": code_text,
                            "date": date_text,
                            "category": category_text or "other",
                        })
            if not event_chain and event_dates:
                risk_event_set = {str(event).strip() for event in risk_events}
                for code_text, date_text in sorted(event_dates.items(), key=lambda item: (item[1], item[0])):
                    event_chain.append({
                        "event": code_text,
                        "date": date_text,
                        "category": "distributionRisk" if code_text in risk_event_set else "accumulation",
                    })
            strategy_signal_context = self._build_strategy_signal_context(
                strategy_id_text,
                snapshot,
                normalized_strategy_params,
            )
            sequence_ok = bool(snapshot["sequence_ok"])
            entry_quality_score = float(snapshot["entry_quality_score"])
            health_score = float(snapshot.get("health_score", entry_quality_score) or entry_quality_score)
            event_score = float(
                snapshot.get(
                    "event_score",
                    snapshot.get("event_strength_score", entry_quality_score),
                )
                or 0.0
            )
            event_strength_score = float(snapshot.get("event_strength_score", event_score) or event_score)
            phase_score = float(snapshot.get("phase_score", entry_quality_score) or entry_quality_score)
            structure_score = float(snapshot.get("structure_score", entry_quality_score) or entry_quality_score)
            trend_score = float(snapshot.get("trend_score", entry_quality_score) or entry_quality_score)
            volatility_score = float(snapshot.get("volatility_score", entry_quality_score) or entry_quality_score)
            slope_stability = float(snapshot.get("slope_stability", 0.0) or 0.0)
            volatility_stability = float(snapshot.get("volatility_stability", 0.0) or 0.0)
            pullback_quality = float(snapshot.get("pullback_quality", 0.0) or 0.0)
            event_background_score = float(snapshot.get("event_background_score", 0.0) or 0.0)
            event_position_score = float(snapshot.get("event_position_score", 0.0) or 0.0)
            event_vol_price_score = float(snapshot.get("event_vol_price_score", 0.0) or 0.0)
            event_confirmation_score = float(snapshot.get("event_confirmation_score", 0.0) or 0.0)
            candle_quality_score = float(snapshot.get("candle_quality_score", 0.0) or 0.0)
            cost_center_shift_score = float(snapshot.get("cost_center_shift_score", 0.0) or 0.0)
            weekly_context_score = float(snapshot.get("weekly_context_score", 0.0) or 0.0)
            weekly_context_multiplier = float(snapshot.get("weekly_context_multiplier", 1.0) or 1.0)
            event_recency_score = float(snapshot.get("event_recency_score", 0.0) or 0.0)
            phase_context_score = float(snapshot.get("phase_context_score", 0.0) or 0.0)
            risk_score = float(snapshot.get("risk_score", 0.0) or 0.0)
            confirmation_status = self._normalize_confirmation_status(snapshot.get("confirmation_status", "unconfirmed"))
            event_grade = self._normalize_event_grade(snapshot.get("event_grade", "C"))
            raw_event_confirmation_map = snapshot.get("event_confirmation_map")
            event_confirmation_map: dict[str, str] = {}
            if isinstance(raw_event_confirmation_map, dict):
                for event_name, status in raw_event_confirmation_map.items():
                    name_text = str(event_name).strip()
                    status_text = str(status).strip().lower()
                    if not name_text or status_text not in {"confirmed", "pending", "failed"}:
                        continue
                    event_confirmation_map[name_text] = status_text
            total_event_count = len(event_chain) if event_chain else len(events) + len(risk_events)

            if strategy_signal_context is not None:
                trigger_name = str(strategy_signal_context["signal_name"])
                trigger_date = str(strategy_signal_context["trigger_date"])
                events = [trigger_name]
                risk_events = []
                event_dates = {trigger_name: trigger_date}
                event_chain = [{"event": trigger_name, "date": trigger_date, "category": "custom"}]
                sequence_ok = bool(strategy_signal_context.get("sequence_ok", True))
                entry_quality_score = float(strategy_signal_context.get("entry_quality_score", entry_quality_score) or 0.0)
                health_score = float(strategy_signal_context.get("health_score", entry_quality_score) or 0.0)
                event_score = float(strategy_signal_context.get("event_score", entry_quality_score) or 0.0)
                event_strength_score = float(strategy_signal_context.get("event_strength_score", entry_quality_score) or 0.0)
                phase_score = float(strategy_signal_context.get("phase_score", entry_quality_score) or 0.0)
                structure_score = float(strategy_signal_context.get("structure_score", entry_quality_score) or 0.0)
                trend_score = float(strategy_signal_context.get("trend_score", entry_quality_score) or 0.0)
                volatility_score = float(strategy_signal_context.get("volatility_score", entry_quality_score) or 0.0)
                candle_quality_score = entry_quality_score
                cost_center_shift_score = entry_quality_score
                weekly_context_score = entry_quality_score
                event_background_score = entry_quality_score
                event_position_score = entry_quality_score
                event_vol_price_score = entry_quality_score
                event_confirmation_score = entry_quality_score
                event_recency_score = entry_quality_score
                phase_context_score = entry_quality_score
                risk_score = 0.0
                event_grade = self._normalize_event_grade(strategy_signal_context.get("event_grade", "B"))
                confirmation_status = self._normalize_confirmation_status(
                    strategy_signal_context.get("confirmation_status", "confirmed")
                )
                event_confirmation_map = {trigger_name: "confirmed"}
                total_event_count = int(strategy_signal_context.get("event_count", 1) or 1)

            if total_event_count < min_event_count:
                continue
            if require_sequence and not sequence_ok:
                continue
            if entry_quality_score < min_score:
                continue
            if health_score < strategy_health_score_min:
                continue
            if event_score < strategy_event_score_min:
                continue
            if not self._event_grade_meets_threshold(grade=event_grade, minimum=strategy_event_grade_min):
                continue
            primary_event_name = (
                str(strategy_signal_context["signal_name"]).strip()
                if strategy_signal_context is not None
                else str(snapshot.get("signal", "")).strip()
            )
            if (
                strategy_require_key_event_confirmation
                and primary_event_name in {"SOS", "LPS", "Spring", "JOC"}
                and event_confirmation_map.get(primary_event_name, "pending") != "confirmed"
            ):
                continue

            signal_tags: list[str] = []
            if entry_quality_score >= 82:
                signal_tags.append("B")
            elif entry_quality_score >= 68:
                signal_tags.append("A")
            else:
                signal_tags.append("C")
            if risk_events:
                signal_tags.append("C")
            if str(snapshot["phase"]).startswith("吸筹D") or str(snapshot["phase"]).startswith("吸筹E"):
                signal_tags.append("B")

            primary, secondary = self._resolve_signal_priority(signal_tags)
            trigger_date = (
                str(strategy_signal_context["trigger_date"])
                if strategy_signal_context is not None
                else str(snapshot["trigger_date"])
            )
            try:
                trigger_dt = datetime.strptime(trigger_date, "%Y-%m-%d")
            except ValueError:
                trigger_dt = datetime.now()
                trigger_date = trigger_dt.strftime("%Y-%m-%d")
            signal_age_days, item_as_of_date = self._compute_signal_age_days(
                symbol=row.symbol,
                trigger_date=trigger_date,
                as_of_date=resolved_as_of_date,
            )
            if resolved_signal_as_of_date is None and item_as_of_date:
                resolved_signal_as_of_date = item_as_of_date
            if signal_age_days < normalized_signal_age_min:
                continue
            if (
                normalized_signal_age_max is not None
                and signal_age_days > normalized_signal_age_max
            ):
                continue
            expire_dt = trigger_dt + timedelta(days=2)
            expire_date = expire_dt.strftime("%Y-%m-%d")
            wyckoff_signal = (
                str(strategy_signal_context.get("signal_name", ""))
                if strategy_signal_context is not None
                else str(snapshot["signal"])
            )
            phase = (
                str(strategy_signal_context.get("phase", snapshot.get("phase", "阶段未明")))
                if strategy_signal_context is not None
                else str(snapshot["phase"])
            )
            phase_hint = (
                str(strategy_signal_context.get("phase_hint", ""))
                if strategy_signal_context is not None
                else str(snapshot["phase_hint"])
            )
            reason = (
                str(strategy_signal_context.get("reason") or "").strip()
                if strategy_signal_context is not None
                else ""
            )
            if not reason:
                reason = f"{phase_hint} 关键事件={wyckoff_signal or '无'}"
            if risk_events:
                reason = f"{reason} 风险={','.join(risk_events)}"
            event_grade_raw = str(event_grade).strip().upper()
            event_grade = event_grade_raw if event_grade_raw in {"A", "B", "C"} else "C"
            raw_event_grade_map = snapshot.get("event_grade_map")
            event_grade_map: dict[str, str] = {}
            if isinstance(raw_event_grade_map, dict):
                for event_name, grade in raw_event_grade_map.items():
                    name_text = str(event_name).strip()
                    grade_text = str(grade).strip().upper()
                    if not name_text or grade_text not in {"A", "B", "C"}:
                        continue
                    event_grade_map[name_text] = grade_text
            if strategy_signal_context is not None:
                event_grade_map = {wyckoff_signal: event_grade}

            items.append(
                SignalResult(
                    symbol=row.symbol,
                    name=row.name,
                    primary_signal=primary,  # type: ignore[arg-type]
                    secondary_signals=secondary,  # type: ignore[arg-type]
                    trigger_date=trigger_date,
                    expire_date=expire_date,
                    signal_age_days=signal_age_days,
                    trigger_reason=reason,
                    priority=3 if primary == "B" else 2 if primary == "A" else 1,
                    wyckoff_phase=phase,
                    wyckoff_signal=wyckoff_signal,
                    structure_hhh=(
                        str(strategy_signal_context.get("structure_hhh") or "FORCE|FLOW|SYNC")
                        if strategy_signal_context is not None
                        else str(snapshot["structure_hhh"])
                    ),
                    wy_event_count=total_event_count,
                    wy_sequence_ok=sequence_ok,
                    entry_quality_score=entry_quality_score,
                    wy_events=[str(event) for event in events],
                    wy_risk_events=[str(event) for event in risk_events],
                    wy_event_dates=event_dates,
                    wy_event_chain=event_chain,
                    phase_hint=phase_hint,
                    scan_mode=mode,
                    event_strength_score=event_strength_score,
                    phase_score=phase_score,
                    structure_score=structure_score,
                    trend_score=trend_score,
                    volatility_score=volatility_score,
                    health_score=health_score,
                    slope_stability=slope_stability,
                    volatility_stability=volatility_stability,
                    pullback_quality=pullback_quality,
                    event_score=event_score,
                    event_grade=event_grade,  # type: ignore[arg-type]
                    event_background_score=event_background_score,
                    event_position_score=event_position_score,
                    event_vol_price_score=event_vol_price_score,
                    event_confirmation_score=event_confirmation_score,
                    candle_quality_score=candle_quality_score,
                    cost_center_shift_score=cost_center_shift_score,
                    weekly_context_score=weekly_context_score,
                    weekly_context_multiplier=weekly_context_multiplier,
                    event_recency_score=event_recency_score,
                    phase_context_score=phase_context_score,
                    risk_score=risk_score,
                    confirmation_status=confirmation_status,  # type: ignore[arg-type]
                    event_confirmation_map=event_confirmation_map,
                    event_grade_map=event_grade_map,
                )
            )
            seen_symbols.add(row.symbol)

        def _base_signal_rank_score(item: SignalResult) -> float:
            return max(0.0, min(100.0, float(item.health_score) * 0.45 + float(item.event_score) * 0.55))

        def _signal_rank_score(item: SignalResult) -> float:
            base_score = _base_signal_rank_score(item)
            row = row_by_symbol.get(str(item.symbol).strip().lower())
            if row is None:
                return base_score
            return max(
                0.0,
                min(
                    100.0,
                    self._strategy_registry.rank_signals(
                        strategy_id=strategy_id_text,
                        signal=item,
                        row=row,
                        params=normalized_strategy_params,
                        fallback_score=base_score,
                    ),
                ),
            )

        items.sort(
            key=lambda item: (
                _signal_rank_score(item),
                item.entry_quality_score,
                item.priority,
                item.wy_event_count,
                item.trigger_date,
            ),
            reverse=True,
        )

        # Apply strategy-level top-N cap (e.g. TrendKing sub-strategies)
        descriptor = self._strategy_registry.get(str(strategy_meta.get("strategy_id", "")))
        if descriptor is not None and descriptor.signal_top_n > 0 and len(items) > descriptor.signal_top_n:
            items = items[: descriptor.signal_top_n]

        degraded = bool(degraded_reason) or any(row.degraded for row in candidates)
        payload = SignalsResponse(
            items=items,
            mode=mode,
            as_of_date=resolved_signal_as_of_date,
            generated_at=self._now_datetime(),
            cache_hit=False,
            degraded=degraded,
            degraded_reason=degraded_reason,
            source_count=source_count,
            strategy_id=str(strategy_meta["strategy_id"]),  # type: ignore[arg-type]
            strategy_version=str(strategy_meta.get("version") or "1.0.0"),
            strategy_params=dict(normalized_strategy_params),
            strategy_params_hash=str(strategy_params_hash),
            notes=capability_notes,
        )
        self._save_signals_runtime_cache(cache_key, payload, now_ts=now_ts)
        if self._is_signals_disk_cache_enabled():
            self._save_signals_disk_cache(cache_key, payload)
        return payload

    def _calc_range_return_and_drawdown(
        self, symbol: str, date_from: str, date_to: str,
    ) -> tuple[float, float]:
        if not date_from or not date_to:
            return 0.0, 0.0
        try:
            candles = self._ensure_candles(symbol)
            if not candles:
                return 0.0, 0.0
            df = date_from
            dt = date_to
            start_close: float | None = None
            peak = 0.0
            max_dd = 0.0
            for c in candles:
                d = str(c.time).strip()
                if d < df:
                    continue
                if start_close is None:
                    start_close = float(c.close)
                    peak = start_close
                close = float(c.close)
                if close > peak:
                    peak = close
                dd = (peak - close) / peak if peak > 0 else 0.0
                if dd > max_dd:
                    max_dd = dd
                if d > dt:
                    break
            if start_close is None or start_close <= 0:
                return 0.0, 0.0
            end_close = float(candles[-1].close) if candles else start_close
            ret_pct = (end_close - start_close) / start_close * 100.0
            return round(ret_pct, 2), round(max_dd * 100.0, 2)
        except Exception:
            return 0.0, 0.0

    @staticmethod
    def _generate_weekdays(date_from: str, date_to: str) -> list[str]:
        from datetime import date, timedelta as _td
        try:
            start = date.fromisoformat(str(date_from).strip())
            end = date.fromisoformat(str(date_to).strip())
        except (ValueError, TypeError):
            return []
        if start > end:
            start, end = end, start
        days: list[str] = []
        cur = start
        while cur <= end:
            if cur.weekday() < 5:
                days.append(cur.isoformat())
            cur += _td(days=1)
        return days

    def _raise_if_cross_validate_cancelled(self, should_cancel: Callable[[], bool] | None) -> None:
        if should_cancel is not None and should_cancel():
            raise CrossValidateCancelledError("交叉验证已停止。")

    _SIGNAL_SCAN_CANCEL_CHECK_BATCH = 8

    def cross_validate_strategies(
        self,
        *,
        request: CrossValidateRequest,
        should_cancel: Callable[[], bool] | None = None,
        on_progress: Callable[[int, int, str, str], None] | None = None,
    ) -> CrossValidateResponse:
        t0 = time.time()
        strategy_descriptor_map: dict[str, str] = {}
        for desc in self._strategy_registry.list():
            strategy_descriptor_map[desc.strategy_id] = desc.name

        has_date_range = bool(request.date_from and request.date_to)
        scan_dates: list[str] = []
        if has_date_range:
            scan_dates = self._generate_weekdays(request.date_from, request.date_to)

        # Accumulate: strategy_id -> symbol -> {best SignalResult, set of dates appeared}
        # In single-date mode, each symbol appears at most once per strategy.
        # In date-range mode, a symbol may appear on multiple dates per strategy.
        StrategyPool = dict[str, tuple[SignalResult, set[str]]]
        pools: dict[str, StrategyPool] = {}
        errors: list[str] = []
        resolved_as_of_date = ""

        if not scan_dates:
            scan_dates = [request.as_of_date or ""]

        strategy_configs = list(request.strategies)
        total_scans = max(1, len(strategy_configs) * len(scan_dates))
        completed_scans = 0

        for config in strategy_configs:
            sid = str(config.strategy_id).strip()
            pool: StrategyPool = {}
            for scan_date in scan_dates:
                self._raise_if_cross_validate_cancelled(should_cancel)
                as_of = scan_date or request.as_of_date or None
                if on_progress is not None:
                    on_progress(completed_scans, total_scans, sid, str(scan_date or as_of or ""))
                try:
                    resp = self.get_signals(
                        mode=config.mode or request.mode,
                        run_id=request.run_id,
                        trend_step=config.trend_step or request.trend_step,
                        strategy_id=sid,
                        strategy_params=config.strategy_params if config.strategy_params else None,
                        market_filters=request.market_filters or None,
                        board_filters=request.board_filters or None,
                        as_of_date=as_of,
                        refresh=False,
                        window_days=request.window_days,
                        min_score=request.min_score,
                        min_event_count=request.min_event_count,
                        should_cancel=should_cancel,
                    )
                except CrossValidateCancelledError:
                    raise
                except (BacktestValidationError, ValueError) as exc:
                    if scan_date == scan_dates[-1]:
                        errors.append(f"策略 {sid} 执行失败: {exc}")
                    completed_scans += 1
                    continue
                completed_scans += 1
                if on_progress is not None:
                    on_progress(completed_scans, total_scans, sid, str(scan_date or as_of or ""))
                if not resolved_as_of_date and resp.as_of_date:
                    resolved_as_of_date = resp.as_of_date
                for item in resp.items:
                    key = str(item.symbol).strip().lower()
                    if key not in pool:
                        pool[key] = (item, set())
                    existing_sr, existing_dates = pool[key]
                    existing_dates.add(str(resp.as_of_date))
                    if float(item.entry_quality_score) > float(existing_sr.entry_quality_score):
                        pool[key] = (item, existing_dates)
            if pool:
                pools[sid] = pool

        warnings: list[str] = list(errors)
        empty_pool_strategies = [
            sid for sid in [str(c.strategy_id).strip() for c in request.strategies]
            if sid not in pools
        ]
        if empty_pool_strategies:
            warnings.append(
                f"以下策略未产出信号池: {', '.join(empty_pool_strategies)}"
            )
        if request.mode == "trend_pool" and not request.run_id:
            warnings.append("趋势池模式需要有效的 Run ID，当前未提供")
        elif request.mode == "trend_pool" and request.run_id:
            if request.run_id not in self._run_store:
                warnings.append(f"Run ID '{request.run_id[:8]}...' 不存在，请先在选股漏斗执行选股")

        if errors and not pools:
            raise ValueError("; ".join(errors))

        all_symbols: set[str] = set()
        for pool in pools.values():
            all_symbols.update(pool.keys())

        results: list[CrossValidateStockResult] = []
        for sym in all_symbols:
            self._raise_if_cross_validate_cancelled(should_cancel)
            overlap_strategies = [sid for sid, pool in pools.items() if sym in pool]
            overlap_count = len(overlap_strategies)
            if overlap_count < request.min_overlap:
                continue
            first_result = next(
                (pools[sid][sym][0] for sid in overlap_strategies if sym in pools[sid]),
                None,
            )
            if first_result is None:
                continue
            details: list[CrossValidateStockDetail] = []
            scores: list[float] = []
            all_dates: set[str] = set()
            for sid in overlap_strategies:
                sr, dates = pools[sid][sym]
                all_dates.update(dates)
                score = float(sr.entry_quality_score)
                scores.append(score)
                details.append(CrossValidateStockDetail(
                    strategy_id=sid,
                    strategy_name=strategy_descriptor_map.get(sid, sid),
                    score=score,
                    health_score=float(sr.health_score),
                    event_score=float(sr.event_score),
                    event_grade=str(sr.event_grade),
                    wyckoff_phase=str(sr.wyckoff_phase),
                    trigger_date=str(sr.trigger_date),
                    trigger_dates=sorted(dates),
                    appearance_days=len(dates),
                    primary_signal=str(sr.primary_signal),
                ))
            best_score = max(scores) if scores else 0.0
            avg_score = sum(scores) / len(scores) if scores else 0.0
            total_appearance = len(all_dates)
            date_range_str = ""
            if has_date_range and all_dates:
                sorted_dates = sorted(all_dates)
                date_range_str = f"{sorted_dates[0]} ~ {sorted_dates[-1]}"
            # Calculate range return and max drawdown
            range_ret, range_dd = 0.0, 0.0
            if has_date_range:
                range_ret, range_dd = self._calc_range_return_and_drawdown(
                    first_result.symbol, request.date_from, request.date_to,
                )
            elif resolved_as_of_date:
                range_ret, range_dd = self._calc_range_return_and_drawdown(
                    first_result.symbol, resolved_as_of_date, resolved_as_of_date,
                )
            results.append(CrossValidateStockResult(
                symbol=first_result.symbol,
                name=first_result.name,
                overlap_count=overlap_count,
                strategy_details=details,
                best_score=round(best_score, 2),
                avg_score=round(avg_score, 2),
                total_appearance_days=total_appearance,
                date_range=date_range_str,
                range_return_pct=range_ret,
                range_max_drawdown_pct=range_dd,
            ))

        if has_date_range:
            results.sort(key=lambda r: (r.overlap_count, r.total_appearance_days, r.best_score, r.avg_score), reverse=True)
        else:
            results.sort(key=lambda r: (r.overlap_count, r.best_score, r.avg_score), reverse=True)

        strategy_pools = {sid: len(pool) for sid, pool in pools.items()}

        return CrossValidateResponse(
            as_of_date=resolved_as_of_date,
            date_from=request.date_from or "",
            date_to=request.date_to or "",
            strategy_pools=strategy_pools,
            results=results,
            total_unique_stocks=len(all_symbols),
            elapsed_sec=round(time.time() - t0, 2),
            warnings=warnings,
        )

    def get_cross_validate_task(self, task_id: str) -> CrossValidateTaskStatusResponse | None:
        normalized = str(task_id or "").strip()
        if not normalized:
            return None
        with self._cross_validate_task_lock:
            return self._cross_validate_tasks.get(normalized)

    def _upsert_cross_validate_task(self, task: CrossValidateTaskStatusResponse) -> None:
        with self._cross_validate_task_lock:
            self._cross_validate_tasks[task.task_id] = task
            if len(self._cross_validate_tasks) > 40:
                oldest = sorted(
                    self._cross_validate_tasks.items(),
                    key=lambda item: item[1].progress.updated_at or "",
                )[: len(self._cross_validate_tasks) - 40]
                for old_id, _ in oldest:
                    self._cross_validate_tasks.pop(old_id, None)
                    self._cross_validate_task_payloads.pop(old_id, None)

    def _cross_validate_task_should_cancel(self, task_id: str) -> bool:
        normalized = str(task_id or "").strip()
        if not normalized:
            return True
        with self._cross_validate_task_lock:
            cancel_event = self._cross_validate_cancel_events.get(normalized)
            if cancel_event is not None and cancel_event.is_set():
                return True
            task = self._cross_validate_tasks.get(normalized)
        return task is None or task.status == "cancelled"

    def _update_cross_validate_task_progress(
        self,
        task_id: str,
        *,
        completed_scans: int,
        total_scans: int,
        current_strategy_id: str,
        current_scan_date: str,
        message: str,
        status: Literal["pending", "running", "succeeded", "failed", "cancelled"] | None = None,
    ) -> None:
        task = self.get_cross_validate_task(task_id)
        if task is None:
            return
        total = max(1, int(total_scans))
        completed = max(0, min(int(completed_scans), total))
        percent = round(completed * 100.0 / total, 1)
        now_text = self._now_datetime()
        next_progress = task.progress.model_copy(
            update={
                "total_scans": total,
                "completed_scans": completed,
                "percent": percent,
                "current_strategy_id": current_strategy_id,
                "current_scan_date": current_scan_date,
                "message": message,
                "updated_at": now_text,
            }
        )
        updates: dict[str, Any] = {"progress": next_progress}
        if status is not None:
            updates["status"] = status
        self._upsert_cross_validate_task(task.model_copy(update=updates))

    def cancel_cross_validate_task(self, task_id: str) -> CrossValidateTaskStatusResponse:
        normalized = str(task_id or "").strip()
        if not normalized:
            raise BacktestValidationError("CROSS_VALIDATE_TASK_NOT_FOUND", "交叉验证任务不存在")
        now_text = self._now_datetime()
        with self._cross_validate_task_lock:
            task = self._cross_validate_tasks.get(normalized)
            if task is None:
                raise BacktestValidationError("CROSS_VALIDATE_TASK_NOT_FOUND", "交叉验证任务不存在")
            if task.status in {"succeeded", "failed", "cancelled"}:
                return task
            task = task.model_copy(
                update={
                    "status": "cancelled",
                    "progress": task.progress.model_copy(
                        update={
                            "message": "任务已停止。",
                            "updated_at": now_text,
                        }
                    ),
                    "error": None,
                    "error_code": None,
                }
            )
            self._cross_validate_tasks[normalized] = task
            cancel_event = self._cross_validate_cancel_events.get(normalized)
            if cancel_event is not None:
                cancel_event.set()
        return task

    def start_cross_validate_task(self, request: CrossValidateRequest) -> str:
        strategy_configs = list(request.strategies)
        scan_dates: list[str] = []
        if request.date_from and request.date_to:
            scan_dates = self._generate_weekdays(request.date_from, request.date_to)
        if not scan_dates:
            scan_dates = [request.as_of_date or ""]
        total_scans = max(1, len(strategy_configs) * len(scan_dates))
        task_id = f"cv_{uuid4().hex[:16]}"
        now_text = self._now_datetime()
        initial_progress = CrossValidateTaskProgress(
            total_scans=total_scans,
            completed_scans=0,
            percent=0.0,
            current_strategy_id="",
            current_scan_date="",
            message="任务已创建，等待执行。",
            started_at=now_text,
            updated_at=now_text,
        )
        with self._cross_validate_task_lock:
            self._cross_validate_task_payloads[task_id] = request.model_copy(deep=True)
            self._cross_validate_cancel_events[task_id] = Event()
        self._upsert_cross_validate_task(
            CrossValidateTaskStatusResponse(
                task_id=task_id,
                status="pending",
                progress=initial_progress,
                result=None,
                error=None,
                error_code=None,
            )
        )
        self._start_cross_validate_task_worker(task_id, request)
        return task_id

    def _start_cross_validate_task_worker(self, task_id: str, request: CrossValidateRequest) -> None:
        with self._cross_validate_task_lock:
            if task_id in self._cross_validate_running_worker_ids:
                return
            self._cross_validate_running_worker_ids.add(task_id)

        def _worker() -> None:
            try:
                task = self.get_cross_validate_task(task_id)
                if task is None or task.status == "cancelled":
                    return
                self._update_cross_validate_task_progress(
                    task_id,
                    completed_scans=0,
                    total_scans=task.progress.total_scans,
                    current_strategy_id="",
                    current_scan_date="",
                    message="交叉验证执行中...",
                    status="running",
                )

                strategy_descriptor_map: dict[str, str] = {
                    desc.strategy_id: desc.name for desc in self._strategy_registry.list()
                }

                def should_cancel() -> bool:
                    return self._cross_validate_task_should_cancel(task_id)

                def on_progress(
                    completed: int,
                    total: int,
                    strategy_id: str,
                    scan_date: str,
                ) -> None:
                    if self._cross_validate_task_should_cancel(task_id):
                        return
                    label = strategy_descriptor_map.get(strategy_id, strategy_id)
                    self._update_cross_validate_task_progress(
                        task_id,
                        completed_scans=completed,
                        total_scans=total,
                        current_strategy_id=strategy_id,
                        current_scan_date=scan_date,
                        message=f"扫描中：{label} · {scan_date or '最新'} ({completed}/{total})",
                        status="running",
                    )

                result = self.cross_validate_strategies(
                    request=request,
                    should_cancel=should_cancel,
                    on_progress=on_progress,
                )
                if self._cross_validate_task_should_cancel(task_id):
                    return
                finished = self.get_cross_validate_task(task_id)
                if finished is None:
                    return
                done_progress = finished.progress.model_copy(
                    update={
                        "completed_scans": finished.progress.total_scans,
                        "percent": 100.0,
                        "message": "交叉验证完成。",
                        "updated_at": self._now_datetime(),
                    }
                )
                self._upsert_cross_validate_task(
                    finished.model_copy(
                        update={
                            "status": "succeeded",
                            "progress": done_progress,
                            "result": result,
                            "error": None,
                            "error_code": None,
                        }
                    )
                )
            except CrossValidateCancelledError:
                current = self.get_cross_validate_task(task_id)
                if current is not None and current.status != "cancelled":
                    self._upsert_cross_validate_task(
                        current.model_copy(
                            update={
                                "status": "cancelled",
                                "progress": current.progress.model_copy(
                                    update={
                                        "message": "任务已停止。",
                                        "updated_at": self._now_datetime(),
                                    }
                                ),
                                "error": None,
                                "error_code": None,
                            }
                        )
                    )
            except Exception as exc:
                current = self.get_cross_validate_task(task_id)
                if current is None or current.status == "cancelled":
                    return
                code = exc.code if isinstance(exc, BacktestValidationError) else "CROSS_VALIDATE_FAILED"
                message = str(exc)
                self._upsert_cross_validate_task(
                    current.model_copy(
                        update={
                            "status": "failed",
                            "progress": current.progress.model_copy(
                                update={
                                    "message": "交叉验证失败。",
                                    "updated_at": self._now_datetime(),
                                }
                            ),
                            "error": message,
                            "error_code": code,
                        }
                    )
                )
            finally:
                with self._cross_validate_task_lock:
                    self._cross_validate_running_worker_ids.discard(task_id)
                    self._cross_validate_cancel_events.pop(task_id, None)

        Thread(target=keep_awake_while(_worker), daemon=True).start()

    # ── Cross-validate history persistence ──

    def save_cross_validate_result(
        self,
        *,
        response: CrossValidateResponse,
        label: str,
        strategy_ids: list[str],
        strategy_names: list[str],
        request_params: dict,
    ) -> CrossValidateHistoryRecord:
        from datetime import datetime
        history_dir = self._resolve_cross_validate_history_dir()
        history_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now()
        record_id = ts.strftime("%Y%m%d_%H%M%S")
        date_label = (
            f"{response.date_from} ~ {response.date_to}"
            if response.date_from and response.date_to
            else response.as_of_date or ts.strftime("%Y-%m-%d")
        )
        mode = str(request_params.get("mode", "full_market"))
        record = CrossValidateHistoryRecord(
            id=record_id,
            label=label or date_label,
            created_at=ts.isoformat(),
            strategy_ids=strategy_ids,
            strategy_names=strategy_names,
            result_count=len(response.results),
            date_label=date_label,
            mode=mode,
            as_of_date=response.as_of_date,
            date_from=response.date_from,
            date_to=response.date_to,
        )
        detail = CrossValidateHistoryDetail(
            record=record,
            response=response,
            request_params=request_params,
        )
        path = history_dir / f"{record_id}.json"
        tmp_path = path.with_suffix(".tmp")
        tmp_path.write_text(
            detail.model_dump_json(indent=2),
            encoding="utf-8",
        )
        tmp_path.replace(path)
        return record

    def list_cross_validate_history(self) -> list[CrossValidateHistoryRecord]:
        history_dir = self._resolve_cross_validate_history_dir()
        if not history_dir.exists():
            return []
        records: list[CrossValidateHistoryRecord] = []
        for path in sorted(history_dir.glob("*.json"), reverse=True):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                records.append(CrossValidateHistoryRecord(**raw.get("record", raw)))
            except Exception:
                continue
        return records

    def get_cross_validate_history(self, record_id: str) -> CrossValidateHistoryDetail | None:
        history_dir = self._resolve_cross_validate_history_dir()
        path = history_dir / f"{record_id}.json"
        if not path.exists():
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
        return CrossValidateHistoryDetail(**raw)

    def delete_cross_validate_history(self, record_id: str) -> bool:
        history_dir = self._resolve_cross_validate_history_dir()
        path = history_dir / f"{record_id}.json"
        if not path.exists():
            return False
        path.unlink()
        return True

    def backtest_cross_validate(
        self,
        *,
        request: CrossValidateBacktestRequest,
    ) -> CrossValidateBacktestResponse:
        import bisect
        import logging
        logger = logging.getLogger(__name__)
        results: list[CrossValidateBacktestStockResult] = []
        for stock in request.stocks:
            try:
                if not stock.trigger_date:
                    logger.warning("backtest skip %s: empty trigger_date", stock.symbol)
                    continue
                candles = self._ensure_candles(stock.symbol)
                if not candles or len(candles) < 2:
                    logger.warning("backtest skip %s: no candles (%s)", stock.symbol, len(candles) if candles else 0)
                    continue
                dates = [str(c.time).strip() for c in candles]
                closes = [float(c.close) for c in candles]
                buy_date = stock.trigger_date
                # T+1 buy: next trading day after trigger_date
                actual_buy_idx = bisect.bisect_right(dates, buy_date)
                if actual_buy_idx >= len(dates):
                    logger.warning("backtest skip %s: trigger %s is last candle, no T+1 (idx=%d, len=%d)", stock.symbol, buy_date, actual_buy_idx, len(dates))
                    continue
                buy_price = closes[actual_buy_idx]
                actual_buy_date = dates[actual_buy_idx]
                if request.hold_until_today:
                    sell_idx = len(dates) - 1
                else:
                    sell_idx = actual_buy_idx + request.hold_days
                    if sell_idx >= len(dates):
                        sell_idx = len(dates) - 1
                sell_price = closes[sell_idx]
                sell_date = dates[sell_idx]
                if buy_price <= 0:
                    continue
                ret_pct = (sell_price - buy_price) / buy_price * 100.0
                results.append(CrossValidateBacktestStockResult(
                    symbol=stock.symbol,
                    name="",
                    buy_date=actual_buy_date,
                    sell_date=sell_date,
                    buy_price=round(buy_price, 2),
                    sell_price=round(sell_price, 2),
                    return_pct=round(ret_pct, 2),
                    is_win=ret_pct > 0,
                ))
            except Exception as exc:
                logger.warning("backtest error for %s: %s", stock.symbol, exc)
                continue
        valid_count = len(results)
        win_count = sum(1 for r in results if r.is_win)
        returns = [r.return_pct for r in results] if results else [0.0]
        returns_sorted = sorted(returns)
        median_ret = returns_sorted[len(returns_sorted) // 2] if returns_sorted else 0.0
        return CrossValidateBacktestResponse(
            hold_days=request.hold_days,
            hold_until_today=request.hold_until_today,
            buy_mode=request.buy_mode,
            total_count=len(request.stocks),
            valid_count=valid_count,
            win_count=win_count,
            win_rate=round(win_count / valid_count * 100, 1) if valid_count > 0 else 0.0,
            avg_return_pct=round(sum(returns) / len(returns), 2) if returns else 0.0,
            median_return_pct=round(median_ret, 2),
            max_return_pct=round(max(returns), 2) if returns else 0.0,
            min_return_pct=round(min(returns), 2) if returns else 0.0,
            stocks=results,
        )

    @staticmethod
    def _detect_primary_board_from_symbol(symbol: str) -> str | None:
        normalized = str(symbol).strip().lower()
        if len(normalized) < 8:
            return None
        market = normalized[:2]
        code = normalized[2:]
        if market == "bj":
            return "beijing"
        if market == "sh":
            if code.startswith("688") or code.startswith("689"):
                return "star"
            return "main"
        if market == "sz":
            if code.startswith("300") or code.startswith("301"):
                return "gem"
            return "main"
        return None

    @staticmethod
    def _is_st_stock(name: str) -> bool:
        normalized_name = re.sub(r"\s+", "", str(name).upper())
        return "ST" in normalized_name

    @staticmethod
    def _row_matches_market_filters(
        row: ScreenerResult,
        market_filters: list[Market],
    ) -> bool:
        if not market_filters:
            return True
        symbol = str(row.symbol).strip().lower()
        if len(symbol) < 2:
            return False
        return symbol[:2] in set(market_filters)

    @classmethod
    def _row_matches_board_filters(
        cls,
        row: ScreenerResult,
        board_filters: list[BoardFilter],
    ) -> bool:
        if not board_filters:
            return True
        selected = set(board_filters)
        is_st = cls._is_st_stock(row.name)
        if is_st and "st" not in selected:
            return False

        selected_boards = [item for item in board_filters if item != "st"]
        if not selected_boards:
            return is_st

        board = cls._detect_primary_board_from_symbol(row.symbol)
        if board is None:
            return False
        return board in selected_boards

    @staticmethod
    def _select_step_source_for_backtest(
        *,
        trend_step: TrendPoolStep,
        step1_pool: list[ScreenerResult],
        step2_pool: list[ScreenerResult],
        step3_pool: list[ScreenerResult],
        step4_pool: list[ScreenerResult],
    ) -> list[ScreenerResult]:
        if trend_step == "step4":
            return step4_pool
        if trend_step == "step3":
            return step3_pool
        if trend_step == "step2":
            return step2_pool
        if trend_step == "step1":
            return step1_pool
        return step4_pool or step3_pool

    @staticmethod
    def _clamp_score_0_100(value: float) -> float:
        return max(0.0, min(100.0, float(value)))

    @staticmethod
    def _compute_trend_health_proxy_score(row: ScreenerResult) -> float:
        ma_structure_score = (
            min(1.0, max(0.0, float(row.ma10_above_ma20_days)) / 10.0) * 0.55
            + min(1.0, max(0.0, float(row.ma5_above_ma10_days)) / 8.0) * 0.45
        ) * 100.0
        retrace_score = InMemoryStore._clamp_score_0_100(100.0 - abs(float(row.retrace20) - 0.12) / 0.18 * 100.0)
        price_band_score = InMemoryStore._clamp_score_0_100(
            100.0 - abs(float(row.price_vs_ma20) - 0.05) / 0.12 * 100.0
        )
        volatility_score = InMemoryStore._clamp_score_0_100(100.0 - max(0.0, float(row.amplitude20)) / 0.15 * 100.0)
        volume_expand_score = InMemoryStore._clamp_score_0_100((float(row.up_down_volume_ratio) - 1.0) / 0.8 * 100.0)
        pullback_volume_score = InMemoryStore._clamp_score_0_100(
            (1.0 - float(row.pullback_volume_ratio)) / 0.4 * 100.0
        )
        volume_quality_score = volume_expand_score * 0.6 + pullback_volume_score * 0.4
        trend_momentum_score = InMemoryStore._clamp_score_0_100(float(row.ret40) / 0.35 * 100.0)
        return InMemoryStore._clamp_score_0_100(
            ma_structure_score * 0.28
            + retrace_score * 0.22
            + price_band_score * 0.14
            + volatility_score * 0.14
            + volume_quality_score * 0.12
            + trend_momentum_score * 0.10
        )

    @staticmethod
    def _compute_trend_overheat_risk_score(row: ScreenerResult) -> float:
        score = 0.0
        if bool(row.has_blowoff_top):
            score += 35.0
        if bool(row.has_divergence_5d):
            score += 25.0
        if bool(row.has_upper_shadow_risk):
            score += 20.0
        score += max(0.0, float(row.amplitude20) - 0.09) * 450.0
        score += max(0.0, float(row.price_vs_ma20) - 0.10) * 360.0
        score += max(0.0, float(row.ret40) - 0.55) * 120.0
        return InMemoryStore._clamp_score_0_100(score)

    @staticmethod
    def _compute_trend_rank_score(row: ScreenerResult) -> float:
        health_score = InMemoryStore._compute_trend_health_proxy_score(row)
        overheat_risk = InMemoryStore._compute_trend_overheat_risk_score(row)
        event_proxy_score = InMemoryStore._clamp_score_0_100(float(row.score))
        theme_bonus = 3.0 if row.theme_stage == "发酵中" else 0.0
        theme_penalty = 4.0 if row.theme_stage == "退潮" else 0.0
        rank_score = (
            health_score * 0.45
            + event_proxy_score * 0.55
            - overheat_risk * 0.25
            + max(0.0, float(row.ai_confidence)) * 6.0
            + theme_bonus
            - theme_penalty
        )
        return InMemoryStore._clamp_score_0_100(rank_score)

    @staticmethod
    def _default_screener_step_configs_from_params(params: ScreenerParams) -> ScreenerStepConfigs:
        return ScreenerStepConfigs(
            step1=ScreenerStep1Config(
                rank_start=1,
                top_n=int(params.top_n),
                turnover_threshold=float(params.turnover_threshold),
                amount_threshold=float(params.amount_threshold),
                amplitude_threshold=float(params.amplitude_threshold),
            ),
            step2=ScreenerStep2Config(),
            step3=ScreenerStep3Config(),
            step4=ScreenerStep4Config(),
        )

    @classmethod
    def _resolve_screener_step_configs(cls, params: ScreenerParams) -> ScreenerStepConfigs:
        base = cls._default_screener_step_configs_from_params(params)
        provided = params.step_configs
        if provided is None:
            return base
        provided_raw = provided.model_dump(exclude_unset=True, exclude_none=True)
        if not provided_raw:
            return base
        merged = base.model_dump()
        for key, value in provided_raw.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key].update(value)
            else:
                merged[key] = value
        resolved = ScreenerStepConfigs(**merged)
        rank_start, rank_end = cls._normalize_screener_step1_rank_window(
            resolved.step1.rank_start,
            resolved.step1.top_n,
        )
        if rank_start == resolved.step1.rank_start and rank_end == resolved.step1.top_n:
            return resolved
        return resolved.model_copy(
            update={
                "step1": resolved.step1.model_copy(
                    update={
                        "rank_start": rank_start,
                        "top_n": rank_end,
                    }
                )
            }
        )

    @staticmethod
    def _normalize_screener_step1_rank_window(rank_start: int | None, rank_end: int | None) -> tuple[int, int]:
        start = max(1, min(2000, int(rank_start or 1)))
        end = max(1, min(2000, int(rank_end or 500)))
        if end < start:
            end = start
        return start, end

    @staticmethod
    def _bind_step_configs_to_screener_params(
        params: ScreenerParams,
        step_configs: ScreenerStepConfigs,
    ) -> ScreenerParams:
        return params.model_copy(
            update={
                "top_n": int(step_configs.step1.top_n),
                "turnover_threshold": float(step_configs.step1.turnover_threshold),
                "amount_threshold": float(step_configs.step1.amount_threshold),
                "amplitude_threshold": float(step_configs.step1.amplitude_threshold),
                "step_configs": step_configs,
            }
        )

    @staticmethod
    def _run_screener_filters_for_backtest(
        rows: list[ScreenerResult],
        *,
        mode: ScreenerMode,
        step_configs: ScreenerStepConfigs,
    ) -> tuple[list[ScreenerResult], list[ScreenerResult], list[ScreenerResult], list[ScreenerResult]]:
        step1_cfg = step_configs.step1
        step2_cfg = step_configs.step2
        step3_cfg = step_configs.step3
        step4_cfg = step_configs.step4

        rank_start, rank_end = InMemoryStore._normalize_screener_step1_rank_window(
            step1_cfg.rank_start,
            step1_cfg.top_n,
        )
        ranked_window = sorted(rows, key=lambda row: row.ret40, reverse=True)[rank_start - 1 : rank_end]
        step1_pool = [
            row
            for row in ranked_window
            if row.turnover20 >= step1_cfg.turnover_threshold
            and row.amount20 >= step1_cfg.amount_threshold
            and row.amplitude20 >= step1_cfg.amplitude_threshold
        ]
        if len(step1_pool) > 400:
            step1_pool = step1_pool[:400]

        loose_padding = 0.02 if mode == "loose" else 0.0
        loose_days = 1 if mode == "loose" else 0
        raw_retrace_min = min(step2_cfg.retrace_min, step2_cfg.retrace_max)
        raw_retrace_max = max(step2_cfg.retrace_min, step2_cfg.retrace_max)
        retrace_min = max(0.0, raw_retrace_min - loose_padding)
        retrace_max = min(0.8, raw_retrace_max + loose_padding)
        max_pullback_days = int(step2_cfg.max_pullback_days) + loose_days
        min_ma10_days = max(0, int(step2_cfg.min_ma10_above_ma20_days) - loose_days)
        min_ma5_days = max(0, int(step2_cfg.min_ma5_above_ma10_days) - loose_days)

        step2_pool = [
            row
            for row in step1_pool
            if row.retrace20 >= retrace_min
            and row.retrace20 <= retrace_max
            and row.pullback_days <= max_pullback_days
            and row.ma10_above_ma20_days >= min_ma10_days
            and row.ma5_above_ma10_days >= min_ma5_days
            and abs(row.price_vs_ma20) <= float(step2_cfg.max_price_vs_ma20)
            and ((not step2_cfg.require_above_ma20) or row.price_vs_ma20 >= 0)
            and (step2_cfg.allow_b_trend or row.trend_class != "B")
        ]

        step3_pool = [
            row
            for row in step2_pool
            if row.vol_slope20 >= float(step3_cfg.min_vol_slope20)
            and row.up_down_volume_ratio >= float(step3_cfg.min_up_down_volume_ratio)
            and row.pullback_volume_ratio <= float(step3_cfg.max_pullback_volume_ratio)
            and (step3_cfg.allow_blowoff_top or not row.has_blowoff_top)
            and (step3_cfg.allow_divergence_5d or not row.has_divergence_5d)
            and (step3_cfg.allow_upper_shadow_risk or not row.has_upper_shadow_risk)
            and (step3_cfg.allow_degraded or not row.degraded)
        ]

        step4_source = step3_pool if step4_cfg.allow_degraded else [row for row in step3_pool if not row.degraded]
        step4_pool = sorted(
            (
                row
                for row in step4_source
                if row.ai_confidence >= float(step4_cfg.min_ai_confidence)
                and row.theme_stage in set(step4_cfg.allowed_theme_stages)
            ),
            key=lambda row: (row.score + row.ai_confidence * 20.0),
            reverse=True,
        )[: step4_cfg.final_top_n]

        return step1_pool, step2_pool, step3_pool, step4_pool

    def _build_backtest_scan_dates(self, date_from: str, date_to: str) -> list[str]:
        start_dt = self._parse_date(date_from)
        end_dt = self._parse_date(date_to)
        if start_dt is None or end_dt is None:
            return []
        if start_dt > end_dt:
            start_dt, end_dt = end_dt, start_dt
        out: list[str] = []
        cursor = start_dt
        while cursor <= end_dt:
            if cursor.weekday() < 5:
                out.append(cursor.strftime("%Y-%m-%d"))
            cursor += timedelta(days=1)
        return out

    def _build_weekly_refresh_dates(self, scan_dates: list[str]) -> list[str]:
        out: list[str] = []
        last_key: tuple[int, int] | None = None
        for day in scan_dates:
            parsed = self._parse_date(day)
            if parsed is None:
                continue
            year, week_no, _ = parsed.isocalendar()
            key = (year, week_no)
            if key != last_key:
                out.append(day)
                last_key = key
        if not out and scan_dates:
            out.append(scan_dates[0])
        return out

    @staticmethod
    def _next_scan_date(scan_dates: list[str], current_date: str) -> str | None:
        for day in scan_dates:
            if day > current_date:
                return day
        return None

    @staticmethod
    def _prev_scan_date(scan_dates: list[str], current_date: str) -> str | None:
        previous: str | None = None
        normalized = str(current_date or "").strip()
        for day in scan_dates:
            if day >= normalized:
                break
            previous = day
        return previous

    @staticmethod
    def _collect_position_mode_refresh_dates(
        scan_dates: list[str],
        *,
        seed_date: str,
        exit_dates: list[str],
    ) -> list[str]:
        refresh_date_set: set[str] = set()
        normalized_seed = str(seed_date or "").strip()
        if normalized_seed:
            refresh_date_set.add(normalized_seed)
        for raw_exit_date in exit_dates:
            exit_date = str(raw_exit_date or "").strip()
            if not exit_date:
                continue
            # 卖出信号日（exit 前一日）刷新，使当日买入信号与待买信号口径一致。
            signal_day = InMemoryStore._prev_scan_date(scan_dates, exit_date)
            if signal_day:
                refresh_date_set.add(signal_day)
            next_day = InMemoryStore._next_scan_date(scan_dates, exit_date)
            if next_day:
                refresh_date_set.add(next_day)
        if not refresh_date_set and scan_dates:
            refresh_date_set.add(scan_dates[0])
        return sorted(refresh_date_set)

    def _resolve_backtest_refresh_dates(
        self,
        *,
        scan_dates: list[str],
        pool_roll_mode: BacktestPoolRollMode,
        refresh_dates: list[str] | None = None,
    ) -> list[str]:
        if not scan_dates:
            return []
        if refresh_dates is None:
            if pool_roll_mode == "weekly":
                refresh_dates_used = self._build_weekly_refresh_dates(scan_dates)
            elif pool_roll_mode == "position":
                refresh_dates_used = [scan_dates[0]]
            else:
                refresh_dates_used = list(scan_dates)
            return refresh_dates_used or [scan_dates[0]]

        scan_date_set = set(scan_dates)
        refresh_dates_used = [day for day in refresh_dates if day in scan_date_set]
        return refresh_dates_used or [scan_dates[0]]

    def _is_backtest_input_pool_cache_enabled(self) -> bool:
        return self._env_flag("TDX_TREND_BACKTEST_INPUT_POOL_CACHE", True)

    @staticmethod
    def _backtest_input_pool_preload_workers() -> int:
        raw = os.getenv("TDX_TREND_BACKTEST_INPUT_POOL_WORKERS", "").strip()
        if raw:
            try:
                return max(1, int(raw))
            except Exception:
                pass
        cpu_count = os.cpu_count() or 4
        return max(1, min(8, cpu_count))

    @staticmethod
    def _backtest_input_pool_runtime_ttl_sec() -> float:
        raw = os.getenv("TDX_TREND_BACKTEST_INPUT_POOL_RUNTIME_TTL_SEC", "").strip()
        if not raw:
            return 15 * 60.0
        try:
            return max(0.0, float(raw))
        except Exception:
            return 15 * 60.0

    @staticmethod
    def _backtest_input_pool_runtime_max_items() -> int:
        raw = os.getenv("TDX_TREND_BACKTEST_INPUT_POOL_RUNTIME_MAX_ITEMS", "").strip()
        if not raw:
            return 512
        try:
            return max(32, int(raw))
        except Exception:
            return 512

    def _load_backtest_input_pool_runtime_cache(
        self,
        cache_key: str,
    ) -> tuple[list[ScreenerResult], str | None] | None:
        ttl_sec = self._backtest_input_pool_runtime_ttl_sec()
        with self._backtest_input_pool_runtime_cache_lock:
            cached = self._backtest_input_pool_runtime_cache.get(cache_key)
            if cached is None:
                return None
            created_at, rows, load_error = cached
            if ttl_sec > 0 and (time.time() - created_at) > ttl_sec:
                self._backtest_input_pool_runtime_cache.pop(cache_key, None)
                return None
            # Invalidate if cached data is from a previous day
            if datetime.fromtimestamp(created_at).strftime("%Y-%m-%d") < datetime.now().strftime("%Y-%m-%d"):
                self._backtest_input_pool_runtime_cache.pop(cache_key, None)
                return None
            return list(rows), load_error

    def _save_backtest_input_pool_runtime_cache(
        self,
        cache_key: str,
        rows: list[ScreenerResult],
        load_error: str | None,
    ) -> None:
        if not rows and (not load_error):
            return
        now_ts = time.time()
        with self._backtest_input_pool_runtime_cache_lock:
            self._backtest_input_pool_runtime_cache[cache_key] = (now_ts, list(rows), load_error)
            max_items = self._backtest_input_pool_runtime_max_items()
            if len(self._backtest_input_pool_runtime_cache) > max_items:
                stale_keys = sorted(
                    self._backtest_input_pool_runtime_cache.items(),
                    key=lambda item: float(item[1][0]),
                )
                overflow = len(self._backtest_input_pool_runtime_cache) - max_items
                for key, _value in stale_keys[:overflow]:
                    self._backtest_input_pool_runtime_cache.pop(key, None)

    def _clear_backtest_input_pool_runtime_cache(self) -> None:
        with self._backtest_input_pool_runtime_cache_lock:
            self._backtest_input_pool_runtime_cache.clear()

    @staticmethod
    def _backtest_input_pool_cache_ttl_sec() -> float:
        raw = os.getenv("TDX_TREND_BACKTEST_INPUT_POOL_CACHE_TTL_SEC", "").strip()
        if not raw:
            return 12 * 3600.0
        try:
            return max(0.0, float(raw))
        except Exception:
            return 12 * 3600.0

    def _build_backtest_input_pool_cache_key(
        self,
        *,
        tdx_root: str,
        markets: list[str],
        return_window_days: int,
        as_of_date: str,
    ) -> str:
        payload = {
            "version": self._BACKTEST_INPUT_POOL_CACHE_VERSION,
            "tdx_root": str(self._resolve_user_path(tdx_root)),
            "tdx_last_trade_date": self._resolve_tdx_last_trade_date(tdx_root),
            "markets": sorted({str(item).strip().lower() for item in markets if str(item).strip()}),
            "return_window_days": int(return_window_days),
            "as_of_date": str(as_of_date).strip(),
        }
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:24]

    def _backtest_input_pool_cache_file(self, cache_key: str) -> Path:
        cache_dir = self._resolve_backtest_input_pool_cache_dir()
        return cache_dir / f"{cache_key}.json"

    def _load_backtest_input_pool_cache(
        self,
        *,
        tdx_root: str,
        markets: list[str],
        return_window_days: int,
        as_of_date: str,
    ) -> tuple[list[ScreenerResult], str | None] | None:
        cache_key = self._build_backtest_input_pool_cache_key(
            tdx_root=tdx_root,
            markets=markets,
            return_window_days=return_window_days,
            as_of_date=as_of_date,
        )
        path = self._backtest_input_pool_cache_file(cache_key)
        if not path.exists():
            return None
        ttl_sec = self._backtest_input_pool_cache_ttl_sec()
        if ttl_sec > 0:
            try:
                mtime = path.stat().st_mtime
                age_sec = max(0.0, time.time() - mtime)
                if age_sec > ttl_sec:
                    return None
                # Invalidate if cached data is from a previous day
                if datetime.fromtimestamp(mtime).strftime("%Y-%m-%d") < datetime.now().strftime("%Y-%m-%d"):
                    return None
            except Exception:
                return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                return None
            cached_last_day = str(payload.get("tdx_last_trade_date") or "").strip()
            current_last_day = self._resolve_tdx_last_trade_date(tdx_root)
            if (
                cached_last_day
                and cached_last_day not in {"__unknown__"}
                and current_last_day not in {"__unknown__"}
                and cached_last_day != current_last_day
            ):
                return None
            rows_raw = payload.get("rows")
            if not isinstance(rows_raw, list):
                return None
            rows: list[ScreenerResult] = []
            for item in rows_raw:
                if not isinstance(item, dict):
                    continue
                try:
                    rows.append(ScreenerResult(**item))
                except Exception:
                    continue
            load_error_raw = payload.get("load_error")
            load_error = str(load_error_raw).strip() if isinstance(load_error_raw, str) and load_error_raw.strip() else None
            return rows, load_error
        except Exception:
            return None

    def _save_backtest_input_pool_cache(
        self,
        *,
        tdx_root: str,
        markets: list[str],
        return_window_days: int,
        as_of_date: str,
        rows: list[ScreenerResult],
        load_error: str | None,
    ) -> bool:
        if not rows:
            return False
        cache_key = self._build_backtest_input_pool_cache_key(
            tdx_root=tdx_root,
            markets=markets,
            return_window_days=return_window_days,
            as_of_date=as_of_date,
        )
        path = self._backtest_input_pool_cache_file(cache_key)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            payload: dict[str, Any] = {
                "schema_version": 1,
                "created_at": self._now_datetime(),
                "as_of_date": str(as_of_date).strip(),
                "tdx_last_trade_date": self._resolve_tdx_last_trade_date(tdx_root),
                "load_error": (str(load_error).strip() if (load_error and str(load_error).strip()) else None),
                "rows": [row.model_dump(exclude_none=True) for row in rows],
            }
            tmp_path = path.with_suffix(".tmp")
            tmp_path.write_text(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            tmp_path.replace(path)
            return True
        except Exception:
            return False

    @staticmethod
    def _normalize_input_pool_cache_as_of_date(as_of_date: str | None) -> str:
        text = str(as_of_date or "").strip()
        if text:
            return text
        return "__latest__"

    def _load_input_pool_rows(
        self,
        *,
        markets: list[str],
        return_window_days: int,
        as_of_date: str | None,
    ) -> tuple[list[ScreenerResult], str | None, bool]:
        tdx_root = self._config.tdx_data_path
        cache_enabled = self._is_backtest_input_pool_cache_enabled()
        cache_as_of_date = self._normalize_input_pool_cache_as_of_date(as_of_date)
        cache_key = self._build_backtest_input_pool_cache_key(
            tdx_root=tdx_root,
            markets=markets,
            return_window_days=return_window_days,
            as_of_date=cache_as_of_date,
        )
        if cache_enabled:
            cached_runtime = self._load_backtest_input_pool_runtime_cache(cache_key)
            if cached_runtime is not None:
                rows_cached, load_error_cached = cached_runtime
                return list(rows_cached), load_error_cached, True

            cached = self._load_backtest_input_pool_cache(
                tdx_root=tdx_root,
                markets=markets,
                return_window_days=return_window_days,
                as_of_date=cache_as_of_date,
            )
            if cached is not None:
                rows_cached, load_error_cached = cached
                self._save_backtest_input_pool_runtime_cache(cache_key, list(rows_cached), load_error_cached)
                return list(rows_cached), load_error_cached, True

        rows, load_error = load_input_pool_from_tdx(
            tdx_root=tdx_root,
            markets=markets,
            return_window_days=return_window_days,
            as_of_date=as_of_date,
            load_timeout_sec=self._screener_input_pool_load_timeout_sec(),
        )
        typed_rows = [row for row in rows if isinstance(row, ScreenerResult)]
        if cache_enabled:
            self._save_backtest_input_pool_runtime_cache(cache_key, typed_rows, load_error)
        if cache_enabled and typed_rows:
            self._save_backtest_input_pool_cache(
                tdx_root=tdx_root,
                markets=markets,
                return_window_days=return_window_days,
                as_of_date=cache_as_of_date,
                rows=typed_rows,
                load_error=load_error,
            )
        return rows, load_error, False

    def _is_backtest_trend_filter_cache_enabled(self) -> bool:
        return self._env_flag("TDX_TREND_BACKTEST_TREND_FILTER_CACHE", True)

    @staticmethod
    def _backtest_trend_filter_cache_ttl_sec() -> float:
        raw = os.getenv("TDX_TREND_BACKTEST_TREND_FILTER_CACHE_TTL_SEC", "").strip()
        if not raw:
            return 24 * 3600.0
        try:
            return max(0.0, float(raw))
        except Exception:
            return 24 * 3600.0

    def _build_backtest_trend_filter_cache_key(
        self,
        *,
        as_of_date: str,
        trend_step: TrendPoolStep,
        board_filters: list[BoardFilter],
        max_symbols: int,
        screener_params: ScreenerParams,
    ) -> str:
        # max_symbols 不参与 cache key — 缓存存储完整列表，读取时按需截断。
        _ = max_symbols
        payload = {
            "version": self._BACKTEST_TREND_FILTER_CACHE_VERSION,
            "as_of_date": str(as_of_date).strip(),
            "trend_step": str(trend_step).strip(),
            "board_filters": sorted({str(item).strip().lower() for item in board_filters if str(item).strip()}),
            "tdx_root": str(self._resolve_user_path(self._config.tdx_data_path)),
            "markets": sorted({str(item).strip().lower() for item in screener_params.markets if str(item).strip()}),
            "mode": str(screener_params.mode).strip(),
            "return_window_days": int(screener_params.return_window_days),
            "top_n": int(screener_params.top_n),
            "rank_start": int(
                screener_params.step_configs.step1.rank_start
                if isinstance(screener_params.step_configs, ScreenerStepConfigs)
                else 1
            ),
            "turnover_threshold": round(float(screener_params.turnover_threshold), 8),
            "amount_threshold": round(float(screener_params.amount_threshold), 4),
            "amplitude_threshold": round(float(screener_params.amplitude_threshold), 8),
        }
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:24]

    def _backtest_trend_filter_cache_file(self, cache_key: str) -> Path:
        cache_dir = self._resolve_backtest_trend_filter_cache_dir()
        return cache_dir / f"{cache_key}.json"

    def _load_backtest_trend_filter_cache(
        self,
        *,
        as_of_date: str,
        trend_step: TrendPoolStep,
        board_filters: list[BoardFilter],
        max_symbols: int,
        screener_params: ScreenerParams,
    ) -> list[str] | None:
        cache_key = self._build_backtest_trend_filter_cache_key(
            as_of_date=as_of_date,
            trend_step=trend_step,
            board_filters=board_filters,
            max_symbols=max_symbols,
            screener_params=screener_params,
        )
        path = self._backtest_trend_filter_cache_file(cache_key)
        if not path.exists():
            return None
        ttl_sec = self._backtest_trend_filter_cache_ttl_sec()
        if ttl_sec > 0:
            try:
                age_sec = max(0.0, time.time() - path.stat().st_mtime)
                if age_sec > ttl_sec:
                    return None
            except Exception:
                return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                return None
            symbols_raw = payload.get("symbols")
            if not isinstance(symbols_raw, list):
                return None
            out: list[str] = []
            seen: set[str] = set()
            for item in symbols_raw:
                symbol = str(item).strip().lower()
                if not symbol or symbol in seen:
                    continue
                seen.add(symbol)
                out.append(symbol)
            # 缓存存储完整列表，按 max_symbols 截断返回
            if max_symbols > 0 and len(out) > max_symbols:
                out = out[:max_symbols]
            return out
        except Exception:
            return None

    def _save_backtest_trend_filter_cache(
        self,
        *,
        as_of_date: str,
        trend_step: TrendPoolStep,
        board_filters: list[BoardFilter],
        max_symbols: int,
        screener_params: ScreenerParams,
        symbols: list[str],
    ) -> bool:
        if not symbols:
            return False
        cache_key = self._build_backtest_trend_filter_cache_key(
            as_of_date=as_of_date,
            trend_step=trend_step,
            board_filters=board_filters,
            max_symbols=max_symbols,
            screener_params=screener_params,
        )
        path = self._backtest_trend_filter_cache_file(cache_key)
        try:
            deduped_symbols = list(dict.fromkeys(str(item).strip().lower() for item in symbols if str(item).strip()))
            if not deduped_symbols:
                return False
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "schema_version": 1,
                "created_at": self._now_datetime(),
                "as_of_date": str(as_of_date).strip(),
                "symbols": deduped_symbols,
            }
            tmp_path = path.with_suffix(".tmp")
            tmp_path.write_text(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            tmp_path.replace(path)
            return True
        except Exception:
            return False

    def _build_backtest_input_pool_fallback_rows(
        self,
        *,
        markets: list[str],
    ) -> list[ScreenerResult]:
        market_set = {
            str(item).strip().lower()
            for item in markets
            if str(item).strip()
        }
        market_set = {
            item
            for item in market_set
            if item in {"sh", "sz", "bj"}
        }
        if not market_set:
            market_set = {"sh", "sz"}

        fallback_count = max(120, int(self._config.top_n))
        source_rows = self._pool_range(0, fallback_count, "strict", "input")
        rows = [
            row
            for row in source_rows
            if str(row.symbol).strip().lower()[:2] in market_set
        ]
        if rows:
            return rows

        if "bj" not in market_set:
            return []

        bj_rows: list[ScreenerResult] = []
        bj_sample_size = max(40, min(200, fallback_count // 2))
        for idx, row in enumerate(source_rows[:bj_sample_size]):
            symbol = f"bj{830000 + idx:06d}"
            bj_rows.append(
                row.model_copy(
                    update={
                        "symbol": symbol,
                        "name": f"北交样本{idx + 1}",
                    }
                )
            )
        return bj_rows

    def _load_backtest_input_rows_by_dates(
        self,
        *,
        tdx_root: str,
        markets: list[str],
        return_window_days: int,
        refresh_dates: list[str],
        progress_callback: Callable[[str, int, int, str], None] | None = None,
    ) -> tuple[dict[str, tuple[list[object], str | None]], dict[str, int]]:
        refresh_unique = list(dict.fromkeys(str(day).strip() for day in refresh_dates if str(day).strip()))
        if not refresh_unique:
            return {}, {"cache_hit_days": 0, "cache_miss_days": 0, "cache_write_days": 0}

        cache_enabled = self._is_backtest_input_pool_cache_enabled()
        out: dict[str, tuple[list[object], str | None]] = {}
        cache_hit_days = 0
        cache_write_days = 0
        pending_days: list[str] = []
        total_days = len(refresh_unique)
        done_days_progress = 0

        def _emit_progress(day: str, done: int, total: int, message: str) -> None:
            if progress_callback is None:
                return
            try:
                progress_callback(day, done, total, message)
            except Exception:
                return

        if cache_enabled:
            for day in refresh_unique:
                cache_key = self._build_backtest_input_pool_cache_key(
                    tdx_root=tdx_root,
                    markets=markets,
                    return_window_days=return_window_days,
                    as_of_date=day,
                )
                cached_runtime = self._load_backtest_input_pool_runtime_cache(cache_key)
                if cached_runtime is not None:
                    rows_cached_runtime, load_error_cached_runtime = cached_runtime
                    out[day] = (list(rows_cached_runtime), load_error_cached_runtime)
                    cache_hit_days += 1
                    continue
                cached = self._load_backtest_input_pool_cache(
                    tdx_root=tdx_root,
                    markets=markets,
                    return_window_days=return_window_days,
                    as_of_date=day,
                )
                if cached is None:
                    pending_days.append(day)
                    continue
                rows_cached, load_error_cached = cached
                out[day] = (list(rows_cached), load_error_cached)
                self._save_backtest_input_pool_runtime_cache(cache_key, list(rows_cached), load_error_cached)
                cache_hit_days += 1
            done_days_progress = cache_hit_days
            if cache_hit_days > 0:
                _emit_progress(
                    refresh_unique[min(len(refresh_unique) - 1, cache_hit_days - 1)],
                    done_days_progress,
                    total_days,
                    f"滚动筛选准备：输入池预加载 {done_days_progress}/{total_days}（cache hit）",
                )
        else:
            pending_days = list(refresh_unique)

        def _load_for_day(as_of_date: str) -> tuple[str, list[object], str | None]:
            rows, load_error = load_input_pool_from_tdx(
                tdx_root=tdx_root,
                markets=markets,
                return_window_days=return_window_days,
                as_of_date=as_of_date,
            )
            typed_rows = [row for row in rows if isinstance(row, ScreenerResult)]
            if not typed_rows and str(load_error or "").strip() == "TDX_PATH_NOT_FOUND":
                fallback_rows = self._build_backtest_input_pool_fallback_rows(markets=markets)
                if fallback_rows:
                    return as_of_date, list(fallback_rows), "TDX_PATH_NOT_FOUND_FALLBACK_MOCK_POOL"
            return as_of_date, typed_rows, load_error

        if len(pending_days) <= 1:
            for day in pending_days:
                as_of_date, rows, load_error = _load_for_day(day)
                out[as_of_date] = (rows, load_error)
                if cache_enabled:
                    cache_key = self._build_backtest_input_pool_cache_key(
                        tdx_root=tdx_root,
                        markets=markets,
                        return_window_days=return_window_days,
                        as_of_date=as_of_date,
                    )
                    typed_rows = [row for row in rows if isinstance(row, ScreenerResult)]
                    self._save_backtest_input_pool_runtime_cache(cache_key, typed_rows, load_error)
                    if self._save_backtest_input_pool_cache(
                        tdx_root=tdx_root,
                        markets=markets,
                        return_window_days=return_window_days,
                        as_of_date=as_of_date,
                        rows=typed_rows,
                        load_error=load_error,
                    ):
                        cache_write_days += 1
                done_days_progress += 1
                _emit_progress(
                    as_of_date,
                    done_days_progress,
                    total_days,
                    f"滚动筛选准备：输入池预加载 {done_days_progress}/{total_days}",
                )
            return out, {
                "cache_hit_days": int(cache_hit_days if cache_enabled else 0),
                "cache_miss_days": int(len(pending_days)),
                "cache_write_days": int(cache_write_days),
            }

        workers = max(1, min(self._backtest_input_pool_preload_workers(), len(pending_days)))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            future_map = {
                executor.submit(_load_for_day, day): day
                for day in pending_days
            }
            for future in as_completed(future_map):
                day = future_map[future]
                try:
                    as_of_date, rows, load_error = future.result()
                    out[as_of_date] = (rows, load_error)
                    if cache_enabled:
                        cache_key = self._build_backtest_input_pool_cache_key(
                            tdx_root=tdx_root,
                            markets=markets,
                            return_window_days=return_window_days,
                            as_of_date=as_of_date,
                        )
                        typed_rows = [row for row in rows if isinstance(row, ScreenerResult)]
                        self._save_backtest_input_pool_runtime_cache(cache_key, typed_rows, load_error)
                        if self._save_backtest_input_pool_cache(
                            tdx_root=tdx_root,
                            markets=markets,
                            return_window_days=return_window_days,
                            as_of_date=as_of_date,
                            rows=typed_rows,
                            load_error=load_error,
                        ):
                            cache_write_days += 1
                except Exception as exc:  # noqa: BLE001
                    out[day] = ([], f"LOADER_EXCEPTION:{type(exc).__name__}")
                    as_of_date = day
                done_days_progress += 1
                _emit_progress(
                    as_of_date,
                    done_days_progress,
                    total_days,
                    f"滚动筛选准备：输入池预加载 {done_days_progress}/{total_days}",
                )
        return out, {
            "cache_hit_days": int(cache_hit_days if cache_enabled else 0),
            "cache_miss_days": int(len(pending_days)),
            "cache_write_days": int(cache_write_days),
        }

    @staticmethod
    def _build_allowed_symbols_by_date(
        *,
        scan_dates: list[str],
        pool_by_refresh_date: dict[str, set[str]],
    ) -> tuple[dict[str, set[str]], set[str], int]:
        allowed_symbols_by_date: dict[str, set[str]] = {}
        symbols_union: set[str] = set()
        empty_days = 0
        active_pool: set[str] = set()
        for day in scan_dates:
            if day in pool_by_refresh_date:
                active_pool = set(pool_by_refresh_date.get(day, set()))
            allowed_today = set(active_pool)
            allowed_symbols_by_date[day] = allowed_today
            if allowed_today:
                symbols_union.update(allowed_today)
            else:
                empty_days += 1
        return allowed_symbols_by_date, symbols_union, empty_days

    def _build_backtest_screener_params_from_config(self) -> ScreenerParams:
        markets = [item for item in self._config.markets if item in {"sh", "sz", "bj"}]
        if not markets:
            markets = ["sh", "sz"]
        return ScreenerParams(
            markets=markets,
            mode="strict",
            as_of_date=None,
            return_window_days=max(5, min(120, int(self._config.return_window_days))),
            top_n=max(10, min(2000, int(self._config.top_n))),
            turnover_threshold=max(0.01, min(0.2, float(self._config.turnover_threshold))),
            amount_threshold=max(5e7, min(5e9, float(self._config.amount_threshold))),
            amplitude_threshold=max(0.01, min(0.15, float(self._config.amplitude_threshold))),
        )

    @staticmethod
    def _build_backtest_param_snapshot_note(
        payload: BacktestRunRequest,
        *,
        resolved_run_id: str | None,
        board_filters: list[BoardFilter],
    ) -> str:
        snapshot_payload = {
            "mode": payload.mode,
            "run_id": (resolved_run_id or payload.run_id or "").strip(),
            "trend_step": payload.trend_step,
            "pool_roll_mode": payload.pool_roll_mode,
            "strategy_id": payload.strategy_id,
            "strategy_params": payload.strategy_params,
            "board_filters": sorted(board_filters),
            "date_from": payload.date_from,
            "date_to": payload.date_to,
            "window_days": int(payload.window_days),
            "min_score": round(float(payload.min_score), 3),
            "require_sequence": bool(payload.require_sequence),
            "min_event_count": int(payload.min_event_count),
            "entry_events": list(payload.entry_events),
            "exit_events": list(payload.exit_events),
            "max_symbols": int(payload.max_symbols),
            "max_positions": int(payload.max_positions),
            "priority_mode": payload.priority_mode,
            "priority_topk_per_day": int(payload.priority_topk_per_day),
            "rank_weight_health": round(float(payload.rank_weight_health), 4),
            "rank_weight_event": round(float(payload.rank_weight_event), 4),
            "health_score_min": round(float(payload.health_score_min), 3),
            "event_score_min": round(float(payload.event_score_min), 3),
            "event_grade_min": payload.event_grade_min,
            "execution_path_preference": payload.execution_path_preference,
            "matrix_event_semantic_version": payload.matrix_event_semantic_version,
            "enforce_t1": bool(payload.enforce_t1),
            "entry_delay_days": int(payload.entry_delay_days),
            "delay_invalidation_enabled": bool(payload.delay_invalidation_enabled),
        }
        raw = json.dumps(snapshot_payload, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
        digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
        return (
            f"参数快照摘要: {digest} "
            f"(mode={payload.mode}, roll={payload.pool_roll_mode}, window={payload.window_days}, "
            f"min_score={payload.min_score}, min_event_count={payload.min_event_count}, "
                f"strategy={payload.strategy_id}, "
                f"entry_delay_days={payload.entry_delay_days}, "
                f"path={payload.execution_path_preference}, "
                f"semantic={payload.matrix_event_semantic_version}, "
                f"max_symbols={payload.max_symbols}, run_id={(resolved_run_id or payload.run_id or 'none')})"
        )

    def _require_backtest_trend_pool_run(self, requested_run_id: str | None) -> ScreenerRunDetail:
        run_id = str(requested_run_id or "").strip()
        if not run_id:
            raise ValueError("趋势池回测必须提供 run_id，请先在选股漏斗或 Signals 页面绑定同一个 run_id。")
        run = self._run_store.get(run_id)
        if run is None:
            raise ValueError(f"筛选任务 {run_id} 不存在或已失效，请重新运行选股漏斗并绑定新的 run_id。")
        return run

    def _resolve_backtest_trend_pool_params(
        self,
        requested_run_id: str | None,
    ) -> tuple[ScreenerParams, str | None, str | None, str | None]:
        run = self._require_backtest_trend_pool_run(requested_run_id)
        step_configs = run.step_configs if isinstance(run.step_configs, ScreenerStepConfigs) else self._resolve_screener_step_configs(run.params)
        normalized_params = self._bind_step_configs_to_screener_params(run.params, step_configs)
        degraded_reason = run.degraded_reason if run.degraded else None
        return normalized_params, run.run_id, degraded_reason, None

    def _build_trend_pool_static_universe_from_run(
        self,
        *,
        payload: BacktestRunRequest,
        run_detail: ScreenerRunDetail,
        board_filters: list[BoardFilter],
    ) -> tuple[list[str], dict[str, set[str]], list[str]]:
        scan_dates = self._build_backtest_scan_dates(payload.date_from, payload.date_to)
        if not scan_dates:
            return [], {}, ["回测区间内无可扫描交易日。"]

        source_rows = self._select_step_source_for_backtest(
            trend_step=payload.trend_step,
            step1_pool=run_detail.step_pools.step1,
            step2_pool=run_detail.step_pools.step2,
            step3_pool=run_detail.step_pools.step3,
            step4_pool=run_detail.step_pools.step4,
        )
        original_count = len(source_rows)
        if board_filters:
            source_rows = [row for row in source_rows if self._row_matches_board_filters(row, board_filters)]

        symbols: list[str] = []
        seen_symbols: set[str] = set()
        for row in source_rows:
            symbol = str(row.symbol).strip().lower()
            if not symbol or symbol in seen_symbols:
                continue
            seen_symbols.add(symbol)
            symbols.append(symbol)
            if len(symbols) >= payload.max_symbols:
                break

        symbol_set = set(symbols)
        allowed_symbols_by_date = {scan_date: set(symbol_set) for scan_date in scan_dates}
        step_label = payload.trend_step if payload.trend_step in {"step1", "step2", "step3", "step4"} else "auto(step4->step3)"
        notes = [
            (
                "候选池构建: 使用筛选任务静态池"
                f"（run_id={run_detail.run_id}, step={step_label}, as_of_date={run_detail.as_of_date or 'unknown'}）。"
            ),
            (
                f"静态股票池数量: {len(symbols)}（原始 {original_count}，"
                f"板块过滤后 {len(source_rows)}，单日上限 {payload.max_symbols}）。"
            ),
        ]
        if payload.pool_roll_mode != "daily":
            notes.append(f"trend_pool 模式固定使用静态池，已忽略 pool_roll_mode={payload.pool_roll_mode}。")
        return symbols, allowed_symbols_by_date, notes

    def _build_trend_pool_rolling_universe(
        self,
        *,
        payload: BacktestRunRequest,
        screener_params: ScreenerParams,
        board_filters: list[BoardFilter],
        refresh_dates: list[str] | None = None,
        progress_callback: Callable[[str, int, int, str], None] | None = None,
    ) -> tuple[list[str], dict[str, set[str]], list[str], list[str], list[str]]:
        scan_dates = self._build_backtest_scan_dates(payload.date_from, payload.date_to)
        if not scan_dates:
            return [], {}, ["回测区间内无可扫描交易日。"], [], []

        refresh_dates_used = self._resolve_backtest_refresh_dates(
            scan_dates=scan_dates,
            pool_roll_mode=payload.pool_roll_mode,
            refresh_dates=refresh_dates,
        )
        total_refresh = max(1, len(refresh_dates_used))
        preload_progress_cap = max(1, total_refresh // 4)

        def _preload_progress(day: str, done: int, total: int, message: str) -> None:
            if progress_callback is None:
                return
            total_safe = max(1, int(total))
            cap = min(total_safe, preload_progress_cap)
            done_safe = max(0, min(int(done), total_safe))
            processed = min(cap, int(round((done_safe / total_safe) * cap)))
            progress_callback(day, processed, total_safe, message)

        pool_by_refresh_date: dict[str, set[str]] = {}
        empty_refresh_days = 0
        loader_error_counter: dict[str, int] = {}
        source_rows_total = 0
        trend_cache_enabled = self._is_backtest_trend_filter_cache_enabled()
        trend_cache_hit_days = 0
        trend_cache_miss_days = 0
        trend_cache_write_days = 0
        resolved_step_configs = self._resolve_screener_step_configs(screener_params)
        pending_refresh_dates: list[str] = []
        if trend_cache_enabled:
            for as_of_date in refresh_dates_used:
                cached_symbols = self._load_backtest_trend_filter_cache(
                    as_of_date=as_of_date,
                    trend_step=payload.trend_step,
                    board_filters=board_filters,
                    max_symbols=payload.max_symbols,
                    screener_params=screener_params,
                )
                if cached_symbols is None:
                    pending_refresh_dates.append(as_of_date)
                    trend_cache_miss_days += 1
                    continue
                pool_by_refresh_date[as_of_date] = set(cached_symbols)
                trend_cache_hit_days += 1
        else:
            pending_refresh_dates = list(refresh_dates_used)

        loaded_rows_by_date: dict[str, tuple[list[object], str | None]] = {}
        loader_cache_stats = {"cache_hit_days": 0, "cache_miss_days": 0, "cache_write_days": 0}
        if pending_refresh_dates:
            loaded_rows_by_date, loader_cache_stats = self._load_backtest_input_rows_by_dates(
                tdx_root=self._config.tdx_data_path,
                markets=screener_params.markets,
                return_window_days=screener_params.return_window_days,
                refresh_dates=pending_refresh_dates,
                progress_callback=_preload_progress,
            )
        for idx, as_of_date in enumerate(refresh_dates_used, start=1):
            if as_of_date in pool_by_refresh_date:
                if progress_callback is not None:
                    progress_callback(
                        as_of_date,
                        idx,
                        len(refresh_dates_used),
                        f"滚动筛选进度 {idx}/{len(refresh_dates_used)}（趋势快照 cache hit）",
                    )
                continue
            input_rows, load_error = loaded_rows_by_date.get(as_of_date, ([], "LOADER_MISS"))
            if load_error:
                loader_error_counter[load_error] = loader_error_counter.get(load_error, 0) + 1
            source_rows_total += len(input_rows)

            if not input_rows:
                pool_by_refresh_date[as_of_date] = set()
                empty_refresh_days += 1
                if progress_callback is not None:
                    progress_callback(
                        as_of_date,
                        idx,
                        len(refresh_dates_used),
                        f"滚动筛选进度 {idx}/{len(refresh_dates_used)}（当日数据为空）",
                    )
                continue

            step1_pool, step2_pool, step3_pool, step4_pool = self._run_screener_filters_for_backtest(
                input_rows,
                mode=screener_params.mode,
                step_configs=resolved_step_configs,
            )
            source = self._select_step_source_for_backtest(
                trend_step=payload.trend_step,
                step1_pool=step1_pool,
                step2_pool=step2_pool,
                step3_pool=step3_pool,
                step4_pool=step4_pool,
            )
            if board_filters:
                source = [row for row in source if self._row_matches_board_filters(row, board_filters)]

            day_symbols_full: list[str] = []
            day_symbols: list[str] = []
            seen_symbols: set[str] = set()
            for row in source:
                symbol = str(row.symbol).strip().lower()
                if not symbol or symbol in seen_symbols:
                    continue
                seen_symbols.add(symbol)
                day_symbols_full.append(symbol)
                if len(day_symbols) < payload.max_symbols:
                    day_symbols.append(symbol)

            pool_by_refresh_date[as_of_date] = set(day_symbols)
            if trend_cache_enabled and day_symbols_full:
                if self._save_backtest_trend_filter_cache(
                    as_of_date=as_of_date,
                    trend_step=payload.trend_step,
                    board_filters=board_filters,
                    max_symbols=payload.max_symbols,
                    screener_params=screener_params,
                    symbols=day_symbols_full,
                ):
                    trend_cache_write_days += 1
            if progress_callback is not None:
                progress_callback(
                    as_of_date,
                    idx,
                    len(refresh_dates_used),
                    f"滚动筛选进度 {idx}/{len(refresh_dates_used)}",
                )

        allowed_symbols_by_date, symbols_union, empty_days = self._build_allowed_symbols_by_date(
            scan_dates=scan_dates,
            pool_by_refresh_date=pool_by_refresh_date,
        )

        mode_label_map = {
            "daily": "每日滚动",
            "weekly": "每周滚动",
            "position": "持仓触发滚动",
        }
        mode_label = mode_label_map.get(payload.pool_roll_mode, "每日滚动")
        avg_source_rows = int(round(source_rows_total / max(1, len(refresh_dates_used))))
        notes = [
            (
                f"候选池构建: {mode_label}（扫描 {len(scan_dates)} 日，刷新 {len(refresh_dates_used)} 次，"
                f"每次刷新平均加载 {avg_source_rows} 只标的）。"
            ),
            f"滚动股票池并集数量: {len(symbols_union)}，单日上限: {payload.max_symbols}。",
        ]
        if empty_refresh_days > 0:
            notes.append(f"有 {empty_refresh_days} 个刷新日当日数据为空。")
        if loader_error_counter:
            parts = [f"{reason} x{count}" for reason, count in sorted(loader_error_counter.items())]
            notes.append(f"刷新日数据加载提示: {'; '.join(parts)}")
        if empty_days > 0:
            notes.append(f"有 {empty_days} 个交易日筛选为空，当日不会产生候选信号。")
        cache_hit_days = int(loader_cache_stats.get("cache_hit_days", 0))
        cache_miss_days = int(loader_cache_stats.get("cache_miss_days", 0))
        cache_write_days = int(loader_cache_stats.get("cache_write_days", 0))
        if (cache_hit_days + cache_miss_days) > 0:
            notes.append(
                f"刷新日输入池缓存: hit {cache_hit_days} / miss {cache_miss_days} / write {cache_write_days}。"
            )
        if trend_cache_enabled and (trend_cache_hit_days + trend_cache_miss_days) > 0:
            notes.append(
                f"趋势快照缓存: hit {trend_cache_hit_days} / miss {trend_cache_miss_days} / write {trend_cache_write_days}。"
            )
        return sorted(symbols_union), allowed_symbols_by_date, notes, scan_dates, refresh_dates_used

    def _build_full_market_rolling_universe(
        self,
        *,
        payload: BacktestRunRequest,
        board_filters: list[BoardFilter],
        refresh_dates: list[str] | None = None,
        progress_callback: Callable[[str, int, int, str], None] | None = None,
    ) -> tuple[list[str], dict[str, set[str]], list[str], list[str], list[str]]:
        scan_dates = self._build_backtest_scan_dates(payload.date_from, payload.date_to)
        if not scan_dates:
            return [], {}, ["回测区间内无可扫描交易日。"], [], []

        refresh_dates_used = self._resolve_backtest_refresh_dates(
            scan_dates=scan_dates,
            pool_roll_mode=payload.pool_roll_mode,
            refresh_dates=refresh_dates,
        )
        total_refresh = max(1, len(refresh_dates_used))
        preload_progress_cap = max(1, total_refresh // 4)

        def _preload_progress(day: str, done: int, total: int, message: str) -> None:
            if progress_callback is None:
                return
            total_safe = max(1, int(total))
            cap = min(total_safe, preload_progress_cap)
            done_safe = max(0, min(int(done), total_safe))
            processed = min(cap, int(round((done_safe / total_safe) * cap)))
            progress_callback(day, processed, total_safe, message)

        markets = [item for item in self._config.markets if item in {"sh", "sz", "bj"}]
        if not markets:
            markets = ["sh", "sz"]

        pool_by_refresh_date: dict[str, set[str]] = {}
        empty_refresh_days = 0
        loader_error_counter: dict[str, int] = {}
        source_rows_total = 0
        protect_limit = max(1, int(self._FULL_MARKET_SYSTEM_PROTECT_LIMIT))
        system_limit_hit_days = 0
        loaded_rows_by_date, loader_cache_stats = self._load_backtest_input_rows_by_dates(
            tdx_root=self._config.tdx_data_path,
            markets=markets,
            return_window_days=max(5, min(120, int(self._config.return_window_days))),
            refresh_dates=refresh_dates_used,
            progress_callback=_preload_progress,
        )

        for idx, as_of_date in enumerate(refresh_dates_used, start=1):
            input_rows, load_error = loaded_rows_by_date.get(as_of_date, ([], "LOADER_MISS"))
            if load_error:
                loader_error_counter[load_error] = loader_error_counter.get(load_error, 0) + 1
            source_rows_total += len(input_rows)

            source = input_rows
            if board_filters:
                source = [row for row in source if self._row_matches_board_filters(row, board_filters)]

            day_symbols: list[str] = []
            seen_symbols: set[str] = set()
            for row in source:
                symbol = str(row.symbol).strip().lower()
                if not symbol or symbol in seen_symbols:
                    continue
                seen_symbols.add(symbol)
                day_symbols.append(symbol)

            day_symbols.sort()
            if len(day_symbols) > protect_limit:
                day_symbols = day_symbols[:protect_limit]
                system_limit_hit_days += 1
            if len(day_symbols) > payload.max_symbols:
                day_symbols = day_symbols[: payload.max_symbols]

            pool_by_refresh_date[as_of_date] = set(day_symbols)
            if not day_symbols:
                empty_refresh_days += 1

            if progress_callback is not None:
                progress_text = f"滚动筛选进度 {idx}/{len(refresh_dates_used)}"
                if not day_symbols:
                    progress_text = f"{progress_text}（当日候选为空）"
                progress_callback(
                    as_of_date,
                    idx,
                    len(refresh_dates_used),
                    progress_text,
                )

        allowed_symbols_by_date, symbols_union, empty_days = self._build_allowed_symbols_by_date(
            scan_dates=scan_dates,
            pool_by_refresh_date=pool_by_refresh_date,
        )

        mode_label_map = {
            "daily": "每日滚动",
            "weekly": "每周滚动",
            "position": "持仓触发滚动",
        }
        mode_label = mode_label_map.get(payload.pool_roll_mode, "每日滚动")
        avg_source_rows = int(round(source_rows_total / max(1, len(refresh_dates_used))))
        notes = [
            (
                f"全市场候选池构建: {mode_label}（扫描 {len(scan_dates)} 日，刷新 {len(refresh_dates_used)} 次，"
                f"每次刷新平均加载 {avg_source_rows} 只标的）。"
            ),
            f"滚动股票池并集数量: {len(symbols_union)}，单日上限: {payload.max_symbols}。",
        ]
        if system_limit_hit_days > 0:
            notes.append(
                f"触发系统保护上限: {protect_limit}（共 {system_limit_hit_days} 个刷新日被截断，仅用于资源保护）。"
            )
        if empty_refresh_days > 0:
            notes.append(f"有 {empty_refresh_days} 个刷新日当日候选为空。")
        if loader_error_counter:
            parts = [f"{reason} x{count}" for reason, count in sorted(loader_error_counter.items())]
            notes.append(f"刷新日数据加载提示: {'; '.join(parts)}")
        if empty_days > 0:
            notes.append(f"有 {empty_days} 个交易日筛选为空，当日不会产生候选信号。")
        cache_hit_days = int(loader_cache_stats.get("cache_hit_days", 0))
        cache_miss_days = int(loader_cache_stats.get("cache_miss_days", 0))
        cache_write_days = int(loader_cache_stats.get("cache_write_days", 0))
        if (cache_hit_days + cache_miss_days) > 0:
            notes.append(
                f"刷新日输入池缓存: hit {cache_hit_days} / miss {cache_miss_days} / write {cache_write_days}。"
            )
        return sorted(symbols_union), allowed_symbols_by_date, notes, scan_dates, refresh_dates_used

    def _is_backtest_matrix_engine_enabled(self) -> bool:
        configured = bool(getattr(self._config, "backtest_matrix_engine_enabled", False))
        return self._env_flag("TDX_TREND_BACKTEST_MATRIX_ENGINE", configured)

    def _is_backtest_matrix_diff_guard_enabled(self) -> bool:
        return self._env_flag("TDX_TREND_BACKTEST_MATRIX_DIFF_GUARD", False)

    @staticmethod
    def _backtest_matrix_diff_guard_trade_count_ratio_max() -> float:
        raw = os.getenv("TDX_TREND_BACKTEST_MATRIX_DIFF_TRADE_COUNT_RATIO_MAX", "").strip()
        if not raw:
            return 0.35
        try:
            return max(0.0, float(raw))
        except Exception:
            return 0.35

    @staticmethod
    def _backtest_matrix_diff_guard_total_return_abs_max() -> float:
        raw = os.getenv("TDX_TREND_BACKTEST_MATRIX_DIFF_TOTAL_RETURN_ABS_MAX", "").strip()
        if not raw:
            return 0.12
        try:
            return max(0.0, float(raw))
        except Exception:
            return 0.12

    @staticmethod
    def _backtest_matrix_diff_guard_win_rate_abs_max() -> float:
        raw = os.getenv("TDX_TREND_BACKTEST_MATRIX_DIFF_WIN_RATE_ABS_MAX", "").strip()
        if not raw:
            return 0.15
        try:
            return max(0.0, float(raw))
        except Exception:
            return 0.15

    @staticmethod
    def _backtest_matrix_diff_guard_max_drawdown_abs_max() -> float:
        raw = os.getenv("TDX_TREND_BACKTEST_MATRIX_DIFF_MAX_DRAWDOWN_ABS_MAX", "").strip()
        if not raw:
            return 0.15
        try:
            return max(0.0, float(raw))
        except Exception:
            return 0.15

    def _evaluate_backtest_matrix_legacy_diff(
        self,
        *,
        matrix_result: BacktestResponse,
        legacy_result: BacktestResponse,
    ) -> tuple[bool, str]:
        matrix_stats = matrix_result.stats
        legacy_stats = legacy_result.stats
        matrix_trade_count = max(0, int(matrix_stats.trade_count))
        legacy_trade_count = max(0, int(legacy_stats.trade_count))
        trade_delta_ratio = abs(matrix_trade_count - legacy_trade_count) / float(max(1, legacy_trade_count))
        total_return_delta = abs(float(matrix_stats.total_return) - float(legacy_stats.total_return))
        win_rate_delta = abs(float(matrix_stats.win_rate) - float(legacy_stats.win_rate))
        max_drawdown_delta = abs(float(matrix_stats.max_drawdown) - float(legacy_stats.max_drawdown))

        trade_ratio_max = self._backtest_matrix_diff_guard_trade_count_ratio_max()
        total_return_abs_max = self._backtest_matrix_diff_guard_total_return_abs_max()
        win_rate_abs_max = self._backtest_matrix_diff_guard_win_rate_abs_max()
        max_drawdown_abs_max = self._backtest_matrix_diff_guard_max_drawdown_abs_max()

        exceeded_reasons: list[str] = []
        if trade_delta_ratio > trade_ratio_max:
            exceeded_reasons.append(f"trade_ratio {trade_delta_ratio:.3f}>{trade_ratio_max:.3f}")
        if total_return_delta > total_return_abs_max:
            exceeded_reasons.append(f"total_return {total_return_delta:.4f}>{total_return_abs_max:.4f}")
        if win_rate_delta > win_rate_abs_max:
            exceeded_reasons.append(f"win_rate {win_rate_delta:.4f}>{win_rate_abs_max:.4f}")
        if max_drawdown_delta > max_drawdown_abs_max:
            exceeded_reasons.append(f"max_drawdown {max_drawdown_delta:.4f}>{max_drawdown_abs_max:.4f}")

        summary = (
            "矩阵偏差守卫检查："
            f"trade_ratio={trade_delta_ratio:.3f}，"
            f"total_return_delta={total_return_delta:.4f}，"
            f"win_rate_delta={win_rate_delta:.4f}，"
            f"max_drawdown_delta={max_drawdown_delta:.4f}"
        )
        if exceeded_reasons:
            return True, f"{summary}；超阈值[{'; '.join(exceeded_reasons)}]"
        return False, f"{summary}；在阈值内"

    def _run_backtest_legacy_shadow_for_matrix_diff_guard(
        self,
        *,
        payload: BacktestRunRequest,
    ) -> BacktestResponse:
        shadow_payload = payload.model_copy(
            update={
                "execution_path_preference": "legacy",
                "enable_advanced_analysis": False,
            }
        )
        return self.run_backtest(shadow_payload)

    @staticmethod
    def _strip_forced_legacy_note(notes: list[str]) -> list[str]:
        out: list[str] = []
        for raw_note in notes:
            note = str(raw_note)
            if "execution_path_preference=legacy" in note:
                continue
            out.append(note)
        return out

    @staticmethod
    def _build_backtest_matrix_windows(payload: BacktestRunRequest) -> tuple[int, ...]:
        return tuple(sorted(set([10, 20, 40, 60, max(20, int(payload.window_days))])))

    @staticmethod
    def _build_backtest_signal_matrix_semantic_signature(payload: BacktestRunRequest) -> str:
        semantic_version = str(payload.matrix_event_semantic_version or "matrix_v1").strip() or "matrix_v1"
        if semantic_version != "aligned_wyckoff_v2":
            return f"semantic={semantic_version}"
        signature_payload = {
            "semantic": semantic_version,
            "window_days": int(payload.window_days),
            "min_score": round(float(payload.min_score), 6),
            "min_event_count": int(payload.min_event_count),
            "require_sequence": bool(payload.require_sequence),
            "entry_events": [str(item).strip() for item in payload.entry_events if str(item).strip()],
            "exit_events": [str(item).strip() for item in payload.exit_events if str(item).strip()],
            "health_score_min": round(float(payload.health_score_min), 6),
            "event_score_min": round(float(payload.event_score_min), 6),
            "event_grade_min": str(payload.event_grade_min or "C").strip().upper() or "C",
            "require_key_event_confirmation": bool(payload.require_key_event_confirmation),
            "event_judgment_profile_hash": str(payload.event_judgment_profile_hash or "").strip(),
        }
        raw = json.dumps(signature_payload, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
        return f"{semantic_version}:{hashlib.sha1(raw.encode('utf-8')).hexdigest()[:24]}"

    def _is_backtest_signal_matrix_runtime_cache_enabled(self) -> bool:
        return self._env_flag("TDX_TREND_BACKTEST_SIGNAL_MATRIX_RUNTIME_CACHE", True)

    @staticmethod
    def _backtest_signal_matrix_runtime_ttl_sec() -> float:
        raw = os.getenv("TDX_TREND_BACKTEST_SIGNAL_MATRIX_RUNTIME_CACHE_TTL_SEC", "").strip()
        if not raw:
            return 15 * 60.0
        try:
            return max(0.0, float(raw))
        except Exception:
            return 15 * 60.0

    @staticmethod
    def _backtest_signal_matrix_runtime_max_items() -> int:
        raw = os.getenv("TDX_TREND_BACKTEST_SIGNAL_MATRIX_RUNTIME_CACHE_MAX_ITEMS", "").strip()
        if not raw:
            return 32
        try:
            return max(1, int(raw))
        except Exception:
            return 32

    @staticmethod
    def _build_backtest_signal_matrix_runtime_cache_key(
        *,
        matrix_cache_key: str,
        top_n: int,
        semantic_signature: str,
    ) -> str:
        return f"{matrix_cache_key}|top_n={int(top_n)}|semantic={semantic_signature}"

    def _load_backtest_signal_matrix_runtime_cache(self, cache_key: str) -> BacktestSignalMatrix | None:
        if not self._is_backtest_signal_matrix_runtime_cache_enabled():
            return None
        ttl_sec = self._backtest_signal_matrix_runtime_ttl_sec()
        with self._backtest_signal_matrix_runtime_cache_lock:
            cached = self._backtest_signal_matrix_runtime_cache.get(cache_key)
            if cached is None:
                return None
            created_at, matrix = cached
            if ttl_sec > 0 and (time.time() - created_at) > ttl_sec:
                self._backtest_signal_matrix_runtime_cache.pop(cache_key, None)
                return None
            return matrix

    def _save_backtest_signal_matrix_runtime_cache(self, cache_key: str, matrix: BacktestSignalMatrix) -> None:
        if not self._is_backtest_signal_matrix_runtime_cache_enabled():
            return
        now_ts = time.time()
        with self._backtest_signal_matrix_runtime_cache_lock:
            self._backtest_signal_matrix_runtime_cache[cache_key] = (now_ts, matrix)
            max_items = self._backtest_signal_matrix_runtime_max_items()
            if len(self._backtest_signal_matrix_runtime_cache) > max_items:
                stale_items = sorted(
                    self._backtest_signal_matrix_runtime_cache.items(),
                    key=lambda item: float(item[1][0]),
                )
                overflow = len(self._backtest_signal_matrix_runtime_cache) - max_items
                for key, _value in stale_items[:overflow]:
                    self._backtest_signal_matrix_runtime_cache.pop(key, None)

    def _clear_backtest_signal_matrix_runtime_cache(self) -> None:
        with self._backtest_signal_matrix_runtime_cache_lock:
            self._backtest_signal_matrix_runtime_cache.clear()

    def _is_backtest_signal_matrix_disk_cache_enabled(self) -> bool:
        return self._env_flag("TDX_TREND_BACKTEST_SIGNAL_MATRIX_DISK_CACHE", True)

    @staticmethod
    def _backtest_signal_matrix_disk_cache_ttl_sec() -> float:
        raw = os.getenv("TDX_TREND_BACKTEST_SIGNAL_MATRIX_CACHE_TTL_SEC", "").strip()
        if not raw:
            return 48 * 3600.0
        try:
            return max(0.0, float(raw))
        except Exception:
            return 48 * 3600.0

    def _build_backtest_signal_matrix_disk_cache_key(
        self,
        *,
        matrix_cache_key: str,
        top_n: int,
        semantic_signature: str,
    ) -> str:
        payload = {
            "version": self._BACKTEST_SIGNAL_MATRIX_CACHE_VERSION,
            "matrix_cache_key": str(matrix_cache_key).strip(),
            "top_n": int(top_n),
            "semantic_signature": str(semantic_signature).strip(),
            "algo": str(self._backtest_matrix_algo_version).strip(),
        }
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:24]

    def _backtest_signal_matrix_disk_cache_file(self, cache_key: str) -> Path:
        return self._resolve_backtest_signal_matrix_cache_dir() / f"{cache_key}.npz"

    def _load_backtest_signal_matrix_disk_cache(
        self,
        *,
        cache_key: str,
        expected_shape: tuple[int, int],
    ) -> BacktestSignalMatrix | None:
        if not self._is_backtest_signal_matrix_disk_cache_enabled():
            return None
        path = self._backtest_signal_matrix_disk_cache_file(cache_key)
        if not path.exists():
            return None
        ttl_sec = self._backtest_signal_matrix_disk_cache_ttl_sec()
        if ttl_sec > 0:
            try:
                age_sec = max(0.0, time.time() - path.stat().st_mtime)
                if age_sec > ttl_sec:
                    return None
            except Exception:
                return None
        bool_fields = (
            "s1",
            "s2",
            "s3",
            "s4",
            "s5",
            "s6",
            "s7",
            "s8",
            "s9",
            "in_pool",
            "buy_signal",
            "sell_signal",
        )
        try:
            with np.load(path, allow_pickle=False) as data:
                out_bool: dict[str, np.ndarray] = {}
                for field in bool_fields:
                    if field not in data:
                        return None
                    arr = np.array(data[field], dtype=bool, copy=True)
                    if arr.shape != expected_shape:
                        return None
                    out_bool[field] = arr
                if "score" not in data:
                    return None
                score = np.array(data["score"], dtype=np.float64, copy=True)
                if score.shape != expected_shape:
                    return None
                sell_signal_label: np.ndarray | None = None
                if "sell_signal_label" in data:
                    sell_signal_label = np.array(data["sell_signal_label"], copy=True)
                    if sell_signal_label.shape != expected_shape:
                        return None
            return BacktestSignalMatrix(
                s1=out_bool["s1"],
                s2=out_bool["s2"],
                s3=out_bool["s3"],
                s4=out_bool["s4"],
                s5=out_bool["s5"],
                s6=out_bool["s6"],
                s7=out_bool["s7"],
                s8=out_bool["s8"],
                s9=out_bool["s9"],
                in_pool=out_bool["in_pool"],
                buy_signal=out_bool["buy_signal"],
                sell_signal=out_bool["sell_signal"],
                score=score,
                sell_signal_label=sell_signal_label,
            )
        except Exception:
            return None

    def _save_backtest_signal_matrix_disk_cache(
        self,
        *,
        cache_key: str,
        matrix: BacktestSignalMatrix,
    ) -> bool:
        if not self._is_backtest_signal_matrix_disk_cache_enabled():
            return False
        path = self._backtest_signal_matrix_disk_cache_file(cache_key)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = path.with_suffix(".tmp.npz")
            np.savez_compressed(
                tmp_path,
                s1=matrix.s1.astype(np.uint8),
                s2=matrix.s2.astype(np.uint8),
                s3=matrix.s3.astype(np.uint8),
                s4=matrix.s4.astype(np.uint8),
                s5=matrix.s5.astype(np.uint8),
                s6=matrix.s6.astype(np.uint8),
                s7=matrix.s7.astype(np.uint8),
                s8=matrix.s8.astype(np.uint8),
                s9=matrix.s9.astype(np.uint8),
                in_pool=matrix.in_pool.astype(np.uint8),
                buy_signal=matrix.buy_signal.astype(np.uint8),
                sell_signal=matrix.sell_signal.astype(np.uint8),
                score=matrix.score.astype(np.float64),
                sell_signal_label=(
                    np.asarray(matrix.sell_signal_label, dtype="<U128")
                    if matrix.sell_signal_label is not None
                    else np.full(matrix.sell_signal.shape, "", dtype="<U1")
                ),
            )
            tmp_path.replace(path)
            return True
        except Exception:
            return False

    def _build_aligned_backtest_signal_matrix(
        self,
        *,
        bundle: MatrixBundle,
        payload: BacktestRunRequest,
        control_callback: Callable[[], None] | None = None,
    ) -> BacktestSignalMatrix:
        t, n = bundle.shape()
        empty_bool = np.zeros((t, n), dtype=bool)
        empty_score = np.zeros((t, n), dtype=np.float64)
        if t <= 0 or n <= 0:
            return BacktestSignalMatrix(
                s1=empty_bool,
                s2=empty_bool,
                s3=empty_bool,
                s4=empty_bool,
                s5=empty_bool,
                s6=empty_bool,
                s7=empty_bool,
                s8=empty_bool,
                s9=empty_bool,
                in_pool=empty_bool,
                buy_signal=empty_bool,
                sell_signal=empty_bool,
                score=empty_score,
                sell_signal_label=None,
            )

        engine = self._build_backtest_engine()
        dates = list(bundle.dates)
        valid_mask = np.asarray(bundle.valid_mask, dtype=bool)
        buy_signal = np.zeros((t, n), dtype=bool)
        sell_signal = np.zeros((t, n), dtype=bool)
        in_pool = np.zeros((t, n), dtype=bool)
        score = np.zeros((t, n), dtype=np.float64)
        s5 = np.zeros((t, n), dtype=bool)
        s7 = np.zeros((t, n), dtype=bool)
        s8 = np.zeros((t, n), dtype=bool)
        sell_signal_label = np.full((t, n), "", dtype="<U128")

        def _safe_float(raw: Any, fallback: float = 0.0) -> float:
            try:
                value = float(raw)
            except Exception:
                return float(fallback)
            if not math.isfinite(value):
                return float(fallback)
            return float(value)

        for col, raw_symbol in enumerate(bundle.symbols):
            symbol = str(raw_symbol).strip().lower()
            if not symbol:
                continue
            for row_index, as_of_date in enumerate(dates):
                if control_callback is not None:
                    control_callback()
                if not bool(valid_mask[row_index, col]):
                    continue
                row = self._build_row_from_candles(symbol, as_of_date)
                if row is None:
                    continue
                snapshot = self._calc_wyckoff_snapshot(
                    row,
                    payload.window_days,
                    as_of_date=as_of_date,
                )
                event_dates = engine._normalize_event_dates(snapshot.get("event_dates"))
                day_exit_events = [
                    event_name
                    for event_name in payload.exit_events
                    if event_dates.get(event_name) == as_of_date
                ]
                if day_exit_events:
                    sell_signal[row_index, col] = True
                    s8[row_index, col] = True
                    sell_signal_label[row_index, col] = " / ".join(day_exit_events)

                day_entry_events = [
                    event_name
                    for event_name in payload.entry_events
                    if event_dates.get(event_name) == as_of_date
                ]
                if not day_entry_events:
                    continue

                event_count = engine._normalize_event_count(snapshot)
                sequence_ok = bool(snapshot.get("sequence_ok"))
                entry_quality_score = _safe_float(snapshot.get("entry_quality_score", 0.0), 0.0)
                health_score = _safe_float(snapshot.get("health_score", entry_quality_score), entry_quality_score)
                event_score = _safe_float(
                    snapshot.get(
                        "event_score",
                        snapshot.get("event_strength_score", entry_quality_score),
                    ),
                    0.0,
                )
                confirmation_status = engine._normalize_confirmation_status(
                    snapshot.get("confirmation_status", "unconfirmed")
                )
                event_grade = engine._normalize_event_grade(snapshot.get("event_grade", "C"))
                if event_count < payload.min_event_count:
                    continue
                if payload.require_sequence and not sequence_ok:
                    continue
                if entry_quality_score < payload.min_score:
                    continue
                if not engine._passes_semantic_score_gates(
                    payload=payload,
                    health_score=health_score,
                    event_score=event_score,
                    event_grade=event_grade,
                    confirmation_status=confirmation_status,
                ):
                    continue

                buy_signal[row_index, col] = True
                in_pool[row_index, col] = True
                score[row_index, col] = max(0.0, min(100.0, entry_quality_score))
                s5[row_index, col] = True
                s7[row_index, col] = True

        score = np.where(valid_mask, score, 0.0)
        return BacktestSignalMatrix(
            s1=empty_bool.copy(),
            s2=empty_bool.copy(),
            s3=empty_bool.copy(),
            s4=empty_bool.copy(),
            s5=s5 & valid_mask,
            s6=empty_bool.copy(),
            s7=s7 & valid_mask,
            s8=s8 & valid_mask,
            s9=empty_bool.copy(),
            in_pool=in_pool & valid_mask,
            buy_signal=buy_signal & valid_mask,
            sell_signal=sell_signal & valid_mask,
            score=score,
            sell_signal_label=sell_signal_label,
        )

    def _build_backtest_engine(self) -> BacktestEngine:
        return BacktestEngine(
            get_candles=self._ensure_candles,
            build_row=self._build_row_from_candles,
            calc_snapshot=lambda row, window_days, as_of_date: self._calc_wyckoff_snapshot(
                row,
                window_days=window_days,
                as_of_date=as_of_date,
            ),
            resolve_symbol_name=self._resolve_symbol_name,
            strategy_signal_filter=lambda strategy_id, row, snapshot, params: self._strategy_registry.generate_signals(
                strategy_id=strategy_id,
                row=row,
                snapshot=snapshot,
                params=params,
            ),
            strategy_signal_context_builder=lambda strategy_id, snapshot, params: self._build_strategy_signal_context(
                strategy_id,
                snapshot,
                params,
            ),
        )

    def _run_matrix_execution(
        self,
        *,
        payload: BacktestRunRequest,
        symbols: list[str],
        allowed_symbols_by_date: dict[str, set[str]] | None,
        progress_callback: Callable[[str, int, int, str], None] | None = None,
        progress_total_dates: int | None = None,
        lightweight_probe: bool = False,
        control_callback: Callable[[], None] | None = None,
    ) -> tuple[BacktestResponse, str]:
        if control_callback is not None:
            control_callback()
        total_start_ts = time.perf_counter()

        matrix_windows = self._build_backtest_matrix_windows(payload)
        matrix_max_lookback_days = max(matrix_windows)
        data_version = (
            f"{self._config.market_data_source}|bars={int(self._config.candles_window_bars)}|"
            f"wy={self._wyckoff_event_data_version}|mode={payload.mode}|roll={payload.pool_roll_mode}"
        )
        cache_key = self._backtest_matrix_engine.build_cache_key(
            symbols=symbols,
            date_from=payload.date_from,
            date_to=payload.date_to,
            data_version=data_version,
            window_set=matrix_windows,
            algo_version=self._backtest_matrix_algo_version,
        )
        incremental_signature = self._backtest_matrix_engine.build_incremental_signature(
            symbols=symbols,
            date_from=payload.date_from,
            max_lookback_days=matrix_max_lookback_days,
            data_version=data_version,
            window_set=matrix_windows,
            algo_version=self._backtest_matrix_algo_version,
        )
        bundle_start_ts = time.perf_counter()
        bundle, cache_hit = self._backtest_matrix_engine.build_bundle(
            symbols=symbols,
            get_candles=self._ensure_candles,
            date_from=payload.date_from,
            date_to=payload.date_to,
            max_lookback_days=matrix_max_lookback_days,
            cache_key=cache_key,
            incremental_signature=incremental_signature,
            use_cache=True,
            control_callback=control_callback,
        )
        bundle_elapsed = time.perf_counter() - bundle_start_ts
        if not lightweight_probe:
            self._emit_backtest_runtime_stage_timing("matrix_build", "矩阵构建", bundle_elapsed)
        bundle_meta = self._backtest_matrix_engine.get_build_meta(cache_key) or {}
        bundle_mode = str(bundle_meta.get("mode") or ("cache_hit" if cache_hit else "full_build")).strip()
        bundle_append_rows = int(bundle_meta.get("append_rows") or 0)
        bundle_base_key = str(bundle_meta.get("base_cache_key") or "").strip()
        if control_callback is not None:
            control_callback()
        if not bundle.dates or not bundle.symbols:
            raise ValueError("矩阵引擎未构建出有效数据，请检查K线覆盖范围。")
        self._validate_backtest_data_coverage_with_matrix_bundle(
            payload,
            symbols,
            bundle,
            scope_label="滚动池并集",
        )

        signal_start_ts = time.perf_counter()
        signal_top_n = max(50, min(2000, int(self._config.top_n)))
        signal_semantic_signature = self._build_backtest_signal_matrix_semantic_signature(payload)
        signal_runtime_cache_key = self._build_backtest_signal_matrix_runtime_cache_key(
            matrix_cache_key=cache_key,
            top_n=signal_top_n,
            semantic_signature=signal_semantic_signature,
        )
        signal_disk_cache_key = self._build_backtest_signal_matrix_disk_cache_key(
            matrix_cache_key=cache_key,
            top_n=signal_top_n,
            semantic_signature=signal_semantic_signature,
        )
        signal_cache_source = "miss"
        signal_matrix = self._load_backtest_signal_matrix_runtime_cache(signal_runtime_cache_key)
        if signal_matrix is not None:
            signal_cache_source = "runtime"
        else:
            signal_matrix = self._load_backtest_signal_matrix_disk_cache(
                cache_key=signal_disk_cache_key,
                expected_shape=bundle.shape(),
            )
            if signal_matrix is not None:
                signal_cache_source = "disk"
                self._save_backtest_signal_matrix_runtime_cache(signal_runtime_cache_key, signal_matrix)
            else:
                signal_matrix = compute_backtest_signal_matrix(
                    bundle,
                    top_n=signal_top_n,
                    event_matrix_builder=(
                        (
                            lambda signal_bundle: self._build_aligned_backtest_signal_matrix(
                                bundle=signal_bundle,
                                payload=payload,
                                control_callback=control_callback,
                            )
                        )
                        if payload.matrix_event_semantic_version == "aligned_wyckoff_v2"
                        else None
                    ),
                    control_callback=control_callback,
                )
                self._save_backtest_signal_matrix_runtime_cache(signal_runtime_cache_key, signal_matrix)
                self._save_backtest_signal_matrix_disk_cache(
                    cache_key=signal_disk_cache_key,
                    matrix=signal_matrix,
                )
        signal_elapsed = time.perf_counter() - signal_start_ts
        if not lightweight_probe:
            self._emit_backtest_runtime_stage_timing("signal_compute", "信号计算", signal_elapsed)
        if control_callback is not None:
            control_callback()
        total_safe = max(1, int(progress_total_dates)) if progress_total_dates is not None else None
        if progress_callback is not None and progress_total_dates is not None:
            # Keep one progress slot for the post-scan execution stage,
            # so long-running matching/analysis does not appear as 100% stalled.
            progress_callback(
                payload.date_to,
                total_safe,
                total_safe + 1,
                "矩阵信号计算完成，开始执行回测撮合...",
            )

        engine = self._build_backtest_engine()
        execute_start_ts = time.perf_counter()
        result = engine.run(
            payload=payload,
            symbols=symbols,
            allowed_symbols_by_date=allowed_symbols_by_date,
            matrix_bundle=bundle,
            matrix_signals=signal_matrix,
            build_equity_curve=not lightweight_probe,
            control_callback=control_callback,
        )
        execute_elapsed = time.perf_counter() - execute_start_ts
        if not lightweight_probe:
            self._emit_backtest_runtime_stage_timing("execution_match", "撮合执行", execute_elapsed)
        if progress_callback is not None and total_safe is not None:
            progress_callback(
                payload.date_to,
                total_safe + 1,
                total_safe + 1,
                "主回测撮合完成，正在整理结果...",
            )
        total_elapsed = time.perf_counter() - total_start_ts

        shape_t, shape_n = bundle.shape()
        matrix_note = (
            "矩阵引擎已启用："
            f"shape={shape_t}x{shape_n}，windows={list(matrix_windows)}，"
            f"cache={'hit' if cache_hit else 'miss'}，signal_cache={signal_cache_source}，"
            f"build={bundle_mode}"
            + (f"(append={bundle_append_rows})" if bundle_append_rows > 0 else "")
            + (f"(base={bundle_base_key[:8]}...)" if bundle_base_key else "")
            + f"，key={cache_key[:12]}...，probe={'light' if lightweight_probe else 'full'}；"
            f"耗时[建矩阵={bundle_elapsed:.2f}s, 算信号={signal_elapsed:.2f}s, 撮合={execute_elapsed:.2f}s, 总计={total_elapsed:.2f}s]"
        )
        return result, matrix_note

    def _resolve_matrix_rolling_universe(
        self,
        *,
        payload: BacktestRunRequest,
        board_filters: list[BoardFilter],
        trend_pool_run: ScreenerRunDetail | None,
        progress_callback: Callable[[str, int, int, str], None] | None,
        control_callback: Callable[[], None] | None,
    ) -> tuple[list[str], dict[str, set[str]], list[str], list[str]]:
        if control_callback is not None:
            control_callback()
        scan_dates = self._build_backtest_scan_dates(payload.date_from, payload.date_to)
        if not scan_dates:
            raise ValueError("回测区间内无可扫描交易日。")

        if payload.mode == "trend_pool":
            if trend_pool_run is None:
                raise ValueError("趋势池筛选任务不可用。")
            screener_params = self._bind_step_configs_to_screener_params(
                trend_pool_run.params,
                trend_pool_run.step_configs,
            )

            def _build(
                refresh_dates: list[str] | None,
                cb: Callable[[str, int, int, str], None] | None,
            ) -> tuple[list[str], dict[str, set[str]], list[str], list[str], list[str]]:
                return self._build_trend_pool_rolling_universe(
                    payload=payload,
                    screener_params=screener_params,
                    board_filters=board_filters,
                    refresh_dates=refresh_dates,
                    progress_callback=cb,
                )

        elif payload.mode == "full_market":

            def _build(
                refresh_dates: list[str] | None,
                cb: Callable[[str, int, int, str], None] | None,
            ) -> tuple[list[str], dict[str, set[str]], list[str], list[str], list[str]]:
                return self._build_full_market_rolling_universe(
                    payload=payload,
                    board_filters=board_filters,
                    refresh_dates=refresh_dates,
                    progress_callback=cb,
                )

        else:
            raise ValueError(f"不支持的回测模式: {payload.mode}")

        if payload.pool_roll_mode == "position":
            seed_refresh_dates = [scan_dates[0]]
            seed_symbols, seed_allowed_by_date, _, _, _ = _build(seed_refresh_dates, None)
            if not seed_symbols:
                raise ValueError("回测股票池为空：持仓触发滚动初始池为空。")
            probe_result, probe_matrix_note = self._run_matrix_execution(
                payload=payload,
                symbols=seed_symbols,
                allowed_symbols_by_date=seed_allowed_by_date,
                progress_callback=None,
                progress_total_dates=None,
                lightweight_probe=True,
                control_callback=control_callback,
            )
            refresh_date_set = self._collect_position_mode_refresh_dates(
                scan_dates,
                seed_date=scan_dates[0],
                exit_dates=[trade.exit_date for trade in probe_result.trades],
            )
            if progress_callback is not None:
                progress_callback(
                    scan_dates[0],
                    0,
                    max(1, len(refresh_date_set)),
                    "持仓触发滚动：正在根据卖出日生成刷新计划...",
                )
            rolling_symbols, rolling_allowed_by_date, notes, _, refresh_dates_used = _build(
                refresh_date_set,
                progress_callback,
            )
            merged_notes = [
                *notes,
                f"持仓触发滚动：首日+卖出信号日/卖出后下一交易日刷新，共 {len(refresh_dates_used)} 次。",
                f"持仓触发滚动预演(轻量): {probe_matrix_note}",
            ]
            return rolling_symbols, rolling_allowed_by_date, merged_notes, scan_dates

        rolling_symbols, rolling_allowed_by_date, notes, _, _ = _build(None, progress_callback)
        return rolling_symbols, rolling_allowed_by_date, list(notes), scan_dates

    def _run_backtest_matrix(
        self,
        *,
        payload: BacktestRunRequest,
        board_filters: list[BoardFilter],
        progress_callback: Callable[[str, int, int, str], None] | None = None,
        control_callback: Callable[[], None] | None = None,
    ) -> BacktestResponse:
        degraded_reason: str | None = None
        resolved_run_id: str | None = None
        trend_pool_run: ScreenerRunDetail | None = None
        if payload.mode == "trend_pool":
            trend_pool_run = self._require_backtest_trend_pool_run(payload.run_id)
            resolved_run_id = trend_pool_run.run_id
            degraded_reason = trend_pool_run.degraded_reason if trend_pool_run.degraded else None

        pool_notes: list[str] = []

        rolling_start_ts = time.perf_counter()
        rolling_symbols, rolling_allowed_by_date, rolling_notes, scan_dates = self._resolve_matrix_rolling_universe(
            payload=payload,
            board_filters=board_filters,
            trend_pool_run=trend_pool_run,
            progress_callback=progress_callback,
            control_callback=control_callback,
        )
        self._emit_backtest_runtime_stage_timing(
            "rolling_universe",
            "候选池构建",
            time.perf_counter() - rolling_start_ts,
        )
        pool_notes = [*pool_notes, *rolling_notes]
        if not rolling_symbols:
            reason_text = "；".join(pool_notes) if pool_notes else "滚动筛选结果为空。"
            raise ValueError(f"回测股票池为空：{reason_text}")

        result, matrix_note = self._run_matrix_execution(
            payload=payload,
            symbols=rolling_symbols,
            allowed_symbols_by_date=rolling_allowed_by_date,
            progress_callback=progress_callback,
            progress_total_dates=max(1, len(scan_dates)),
            control_callback=control_callback,
        )

        notes = [matrix_note, *pool_notes, *list(result.notes)]
        if board_filters:
            roll_mode_label = {
                "daily": "每日滚动",
                "weekly": "每周滚动",
                "position": "持仓触发滚动",
            }.get(payload.pool_roll_mode, "每日滚动")
            notes.insert(0, f"候选池板块过滤: {','.join(board_filters)}（{roll_mode_label}生效）")
        if payload.mode == "trend_pool" and resolved_run_id:
            notes.insert(0, f"使用筛选任务: {resolved_run_id}")
        notes.insert(
            0,
            self._build_backtest_param_snapshot_note(
                payload,
                resolved_run_id=resolved_run_id,
                board_filters=board_filters,
            ),
        )
        if degraded_reason:
            notes.append(f"候选池降级原因: {degraded_reason}")
        if notes != result.notes:
            result = result.model_copy(update={"notes": notes})
        return result

    def _summarize_backtest_candle_coverage(
        self,
        *,
        symbols: list[str],
        date_from: str,
        date_to: str,
    ) -> tuple[str | None, str | None, int, int, int]:
        effective_start: str | None = None
        effective_end: str | None = None
        covered_from_count = 0
        covered_to_count = 0
        checked_count = 0

        for raw_symbol in symbols:
            symbol = str(raw_symbol).strip().lower()
            if not symbol:
                continue
            candles = self._ensure_candles(symbol)
            if not candles:
                continue
            first_date = str(candles[0].time).strip()
            last_date = str(candles[-1].time).strip()
            if not first_date or not last_date:
                continue
            checked_count += 1
            if effective_start is None or first_date < effective_start:
                effective_start = first_date
            if effective_end is None or last_date > effective_end:
                effective_end = last_date
            if first_date <= date_from <= last_date:
                covered_from_count += 1
            if first_date <= date_to <= last_date:
                covered_to_count += 1

        return effective_start, effective_end, covered_from_count, covered_to_count, checked_count

    @staticmethod
    def _summarize_backtest_candle_coverage_from_matrix_bundle(
        *,
        symbols: list[str],
        date_from: str,
        date_to: str,
        matrix_bundle: MatrixBundle,
    ) -> tuple[str | None, str | None, int, int, int]:
        dates = list(matrix_bundle.dates)
        valid_mask = matrix_bundle.valid_mask
        if not dates or valid_mask.size <= 0:
            return None, None, 0, 0, 0
        if valid_mask.ndim != 2 or valid_mask.shape[0] != len(dates):
            return None, None, 0, 0, 0

        has_data = np.any(valid_mask, axis=0)
        if not bool(np.any(has_data)):
            return None, None, 0, 0, 0
        first_indexes = np.argmax(valid_mask, axis=0)
        last_indexes = (valid_mask.shape[0] - 1) - np.argmax(valid_mask[::-1, :], axis=0)

        symbol_to_col = matrix_bundle.symbol_to_index()
        effective_start: str | None = None
        effective_end: str | None = None
        covered_from_count = 0
        covered_to_count = 0
        checked_count = 0

        for raw_symbol in symbols:
            symbol = str(raw_symbol).strip().lower()
            if not symbol:
                continue
            col = symbol_to_col.get(symbol)
            if col is None or (not bool(has_data[col])):
                continue
            first_date = str(dates[int(first_indexes[col])]).strip()
            last_date = str(dates[int(last_indexes[col])]).strip()
            if not first_date or not last_date:
                continue
            checked_count += 1
            if effective_start is None or first_date < effective_start:
                effective_start = first_date
            if effective_end is None or last_date > effective_end:
                effective_end = last_date
            if first_date <= date_from <= last_date:
                covered_from_count += 1
            if first_date <= date_to <= last_date:
                covered_to_count += 1

        return effective_start, effective_end, covered_from_count, covered_to_count, checked_count

    def _validate_backtest_data_coverage_by_summary(
        self,
        payload: BacktestRunRequest,
        *,
        summary: tuple[str | None, str | None, int, int, int],
        scope_label: str,
    ) -> None:
        (
            effective_start,
            effective_end,
            covered_from_count,
            covered_to_count,
            checked_count,
        ) = summary
        if checked_count <= 0:
            raise BacktestValidationError(
                "BACKTEST_DATA_COVERAGE_INSUFFICIENT",
                (
                    "回测K线覆盖不足：候选池未读取到可用K线。"
                    "请检查行情源与数据路径。"
                ),
            )

        covered_from_ratio = covered_from_count / checked_count
        covered_to_ratio = covered_to_count / checked_count
        min_coverage_ratio = self._backtest_precheck_min_symbol_coverage_ratio()
        coverage_insufficient = (
            effective_start is None
            or effective_end is None
            or covered_from_count <= 0
            or covered_to_count <= 0
            or covered_from_ratio < min_coverage_ratio
            or covered_to_ratio < min_coverage_ratio
            or effective_start > payload.date_from
            or effective_end < payload.date_to
        )
        if not coverage_insufficient:
            return

        raise BacktestValidationError(
            "BACKTEST_DATA_COVERAGE_INSUFFICIENT",
            (
                f"回测K线覆盖不足：请求区间 {payload.date_from} ~ {payload.date_to}，"
                f"{scope_label}在当前K线窗口(candles_window_bars={int(self._config.candles_window_bars)})下"
                f"有效覆盖约 {effective_start or 'N/A'} ~ {effective_end or 'N/A'}；"
                f"可覆盖起始日标的 {covered_from_count}/{checked_count}，"
                f"可覆盖结束日标的 {covered_to_count}/{checked_count}。"
                f"覆盖比例(起始/结束)={covered_from_ratio:.1%}/{covered_to_ratio:.1%}，"
                f"最低要求={min_coverage_ratio:.1%}。"
                "请增大K线窗口或缩短回测区间。"
            ),
        )

    def _validate_backtest_data_coverage_with_matrix_bundle(
        self,
        payload: BacktestRunRequest,
        symbols: list[str],
        matrix_bundle: MatrixBundle,
        *,
        scope_label: str,
    ) -> None:
        summary = self._summarize_backtest_candle_coverage_from_matrix_bundle(
            symbols=symbols,
            date_from=payload.date_from,
            date_to=payload.date_to,
            matrix_bundle=matrix_bundle,
        )
        self._validate_backtest_data_coverage_by_summary(
            payload,
            summary=summary,
            scope_label=scope_label,
        )

    def _validate_backtest_data_coverage(
        self,
        payload: BacktestRunRequest,
        symbols: list[str],
        *,
        scope_label: str,
    ) -> None:
        summary = self._summarize_backtest_candle_coverage(
            symbols=symbols,
            date_from=payload.date_from,
            date_to=payload.date_to,
        )
        self._validate_backtest_data_coverage_by_summary(
            payload,
            summary=summary,
            scope_label=scope_label,
        )

    def _precheck_backtest_data_coverage_before_task(self, payload: BacktestRunRequest) -> None:
        scan_dates = self._build_backtest_scan_dates(payload.date_from, payload.date_to)
        if not scan_dates:
            raise BacktestValidationError("BACKTEST_INVALID", "回测区间内无可扫描交易日。")
        required_bars = max(1, len(scan_dates)) + max(20, int(payload.window_days)) + 5
        configured_bars = int(self._config.candles_window_bars or 0)
        if configured_bars > 0 and configured_bars < required_bars:
            raise BacktestValidationError(
                "BACKTEST_CANDLES_WINDOW_TOO_SHORT",
                (
                    f"回测K线窗口不足：请求区间 {payload.date_from} ~ {payload.date_to} "
                    f"共 {len(scan_dates)} 个交易日，signal_window={int(payload.window_days)}；"
                    f"估算至少需要 {required_bars} 根K线，当前 candles_window_bars={configured_bars}。"
                    "请在设置中提高K线数后重试。"
                ),
            )
        first_refresh_date = [scan_dates[0]]
        board_filters = [item for item in payload.board_filters if item in {"main", "gem", "star", "beijing", "st"}]

        if payload.mode == "trend_pool":
            trend_pool_run = self._require_backtest_trend_pool_run(payload.run_id)
            trend_pool_params = self._bind_step_configs_to_screener_params(
                trend_pool_run.params,
                trend_pool_run.step_configs,
            )
            seed_symbols, _, _, _, _ = self._build_trend_pool_rolling_universe(
                payload=payload,
                screener_params=trend_pool_params,
                board_filters=board_filters,
                refresh_dates=first_refresh_date,
                progress_callback=None,
            )
        elif payload.mode == "full_market":
            seed_symbols, _, _, _, _ = self._build_full_market_rolling_universe(
                payload=payload,
                board_filters=board_filters,
                refresh_dates=first_refresh_date,
                progress_callback=None,
            )
        else:
            raise BacktestValidationError("BACKTEST_INVALID", f"不支持的回测模式: {payload.mode}")

        if not seed_symbols:
            return
        self._validate_backtest_data_coverage(
            payload,
            seed_symbols,
            scope_label="首个刷新日候选池",
        )

    def _is_backtest_task_precheck_async_enabled(self) -> bool:
        # 默认异步预检，避免任务创建接口在冷缓存/大数据量下同步阻塞超时。
        return self._env_flag("TDX_TREND_BACKTEST_TASK_PRECHECK_ASYNC", True)

    @staticmethod
    def _backtest_precheck_cache_ttl_sec() -> float:
        raw = os.getenv("TDX_TREND_BACKTEST_PRECHECK_CACHE_TTL_SEC", "").strip()
        if not raw:
            return 10 * 60.0
        try:
            return max(0.0, float(raw))
        except Exception:
            return 10 * 60.0

    @staticmethod
    def _backtest_precheck_min_symbol_coverage_ratio() -> float:
        raw = os.getenv("TDX_TREND_BACKTEST_PRECHECK_MIN_SYMBOL_COVERAGE", "").strip()
        if not raw:
            return 0.08
        try:
            parsed = float(raw)
        except Exception:
            return 0.08
        return max(0.0, min(1.0, parsed))

    @staticmethod
    def _build_backtest_task_stage_timing(
        stage_key: str,
        label: str,
        elapsed_sec: float,
    ) -> BacktestTaskStageTiming:
        elapsed_ms = max(0, int(round(float(max(0.0, elapsed_sec)) * 1000.0)))
        return BacktestTaskStageTiming(
            stage_key=str(stage_key or "").strip() or "unknown",
            label=str(label or "").strip() or "未命名阶段",
            elapsed_ms=elapsed_ms,
        )

    @staticmethod
    def _estimate_backtest_advanced_progress_slots(payload: BacktestRunRequest) -> int:
        if not bool(payload.enable_advanced_analysis):
            return 0
        # 高级分析固定拆成 7 个里程碑，避免主回测结束后进度长期停滞。
        return 7

    def _estimate_backtest_scan_progress_total_dates(self, payload: BacktestRunRequest) -> int:
        scan_dates = self._build_backtest_scan_dates(payload.date_from, payload.date_to)
        if not scan_dates:
            return 1
        if payload.pool_roll_mode == "weekly":
            return max(1, len(self._build_weekly_refresh_dates(scan_dates)))
        if payload.pool_roll_mode == "position":
            return max(1, len(scan_dates))
        return max(1, len(scan_dates))

    def _set_backtest_runtime_stage_timing_callback(
        self,
        callback: Callable[[str, str, float], None] | None,
    ) -> None:
        setattr(self._backtest_runtime_context, "stage_timing_callback", callback)

    def _get_backtest_runtime_stage_timing_callback(self) -> Callable[[str, str, float], None] | None:
        callback = getattr(self._backtest_runtime_context, "stage_timing_callback", None)
        return callback if callable(callback) else None

    def _clear_backtest_runtime_stage_timing_callback(self) -> None:
        if hasattr(self._backtest_runtime_context, "stage_timing_callback"):
            delattr(self._backtest_runtime_context, "stage_timing_callback")

    def _emit_backtest_runtime_stage_timing(
        self,
        stage_key: str,
        label: str,
        elapsed_sec: float,
    ) -> None:
        callback = getattr(self._backtest_runtime_context, "stage_timing_callback", None)
        if callback is None:
            return
        try:
            callback(
                str(stage_key or "").strip(),
                str(label or "").strip(),
                float(max(0.0, elapsed_sec)),
            )
        except Exception:
            return

    def _extract_backtest_stage_timings(
        self,
        result: BacktestResponse,
        *,
        run_elapsed_sec: float,
    ) -> list[BacktestTaskStageTiming]:
        notes = list(result.notes)
        matrix_match: re.Match[str] | None = None
        exec_detail_match: re.Match[str] | None = None
        for note in notes:
            if not note:
                continue
            text = str(note)
            if matrix_match is None:
                matrix_match = self._BACKTEST_MATRIX_TIMING_RE.search(text)
            if exec_detail_match is None:
                exec_detail_match = self._BACKTEST_EXEC_DETAIL_TIMING_RE.search(text)
            if matrix_match is not None and exec_detail_match is not None:
                break

        if matrix_match is None:
            return [
                self._build_backtest_task_stage_timing(
                    "run_total",
                    "回测执行",
                    run_elapsed_sec,
                )
            ]

        try:
            matrix_build = float(matrix_match.group("matrix"))
            signal_compute = float(matrix_match.group("signal"))
            execute_match = float(matrix_match.group("match"))
            matrix_total = float(matrix_match.group("total"))
        except Exception:
            return [
                self._build_backtest_task_stage_timing(
                    "run_total",
                    "回测执行",
                    run_elapsed_sec,
                )
            ]

        stage_rows: list[BacktestTaskStageTiming] = []
        pool_overhead = max(0.0, float(run_elapsed_sec) - float(matrix_total))
        if pool_overhead >= 0.02:
            stage_rows.append(
                self._build_backtest_task_stage_timing(
                    "rolling_universe",
                    "候选池构建",
                    pool_overhead,
                )
            )
        stage_rows.append(self._build_backtest_task_stage_timing("matrix_build", "矩阵构建", matrix_build))
        stage_rows.append(self._build_backtest_task_stage_timing("signal_compute", "信号计算", signal_compute))
        if exec_detail_match is not None:
            try:
                candidate_elapsed = float(exec_detail_match.group("candidate"))
                execution_elapsed = float(exec_detail_match.group("match"))
                curve_elapsed = float(exec_detail_match.group("curve"))
            except Exception:
                candidate_elapsed = 0.0
                execution_elapsed = float(execute_match)
                curve_elapsed = 0.0
            stage_rows.extend(
                [
                    self._build_backtest_task_stage_timing("candidate_build", "候选生成", candidate_elapsed),
                    self._build_backtest_task_stage_timing("execution_match", "撮合执行", execution_elapsed),
                    self._build_backtest_task_stage_timing("equity_curve", "权益曲线", curve_elapsed),
                ]
            )
        else:
            stage_rows.append(self._build_backtest_task_stage_timing("execution_match", "撮合执行", execute_match))
        stage_rows.append(
            self._build_backtest_task_stage_timing("run_total", "回测总耗时", max(run_elapsed_sec, matrix_total))
        )
        return stage_rows

    def _build_backtest_precheck_cache_key(self, payload: BacktestRunRequest) -> str:
        payload_raw = payload.model_dump(exclude_none=True)
        board_filters = payload_raw.get("board_filters")
        if isinstance(board_filters, list):
            payload_raw["board_filters"] = sorted(
                {str(item).strip().lower() for item in board_filters if str(item).strip()}
            )
        cache_payload = {
            "version": self._BACKTEST_PRECHECK_CACHE_VERSION,
            "payload": payload_raw,
            "config": {
                "tdx_root": str(self._resolve_user_path(self._config.tdx_data_path)),
                "market_data_source": str(self._config.market_data_source).strip(),
                "candles_window_bars": int(self._config.candles_window_bars),
                "markets": sorted({str(item).strip().lower() for item in self._config.markets if str(item).strip()}),
                "return_window_days": int(self._config.return_window_days),
                "top_n": int(self._config.top_n),
            },
            "algo": {
                "matrix_algo_version": str(self._backtest_matrix_algo_version).strip(),
                "wyckoff_algo_version": str(self._wyckoff_event_algo_version).strip(),
                "wyckoff_data_version": str(self._wyckoff_event_data_version).strip(),
            },
        }
        raw = json.dumps(cache_payload, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:24]

    def _load_backtest_precheck_cache(
        self,
        cache_key: str,
    ) -> tuple[str | None, str | None] | None:
        ttl_sec = self._backtest_precheck_cache_ttl_sec()
        with self._backtest_precheck_cache_lock:
            cached = self._backtest_precheck_cache.get(cache_key)
            if cached is None:
                return None
            created_at, error_code, error_message = cached
            if ttl_sec > 0 and (time.time() - created_at) > ttl_sec:
                self._backtest_precheck_cache.pop(cache_key, None)
                return None
            return error_code, error_message

    def _save_backtest_precheck_cache(
        self,
        cache_key: str,
        *,
        error_code: str | None,
        error_message: str | None,
    ) -> None:
        now_ts = time.time()
        with self._backtest_precheck_cache_lock:
            self._backtest_precheck_cache[cache_key] = (now_ts, error_code, error_message)
            if len(self._backtest_precheck_cache) > 256:
                stale_items = sorted(
                    self._backtest_precheck_cache.items(),
                    key=lambda item: float(item[1][0]),
                )
                overflow = len(self._backtest_precheck_cache) - 256
                for key, _value in stale_items[:overflow]:
                    self._backtest_precheck_cache.pop(key, None)

    def _clear_backtest_precheck_cache(self) -> None:
        with self._backtest_precheck_cache_lock:
            self._backtest_precheck_cache.clear()

    @staticmethod
    def _is_backtest_runtime_auto_trim_enabled() -> bool:
        raw = os.getenv("TDX_TREND_BACKTEST_AUTO_TRIM_RUNTIME", "").strip().lower()
        if not raw:
            return True
        if raw in {"1", "true", "yes", "y", "on"}:
            return True
        if raw in {"0", "false", "no", "n", "off"}:
            return False
        return True

    def _is_backtest_runtime_idle(self) -> bool:
        with self._backtest_task_lock:
            backtest_running = bool(self._backtest_running_worker_ids)
        with self._backtest_plateau_task_lock:
            plateau_running = bool(self._backtest_plateau_running_worker_ids)
        return (not backtest_running) and (not plateau_running)

    def maybe_trim_backtest_runtime_memory(self, *, force: bool = False) -> None:
        if (not force) and (not self._is_backtest_runtime_auto_trim_enabled()):
            return
        if (not force) and (not self._is_backtest_runtime_idle()):
            return
        try:
            self._backtest_matrix_engine.clear_runtime_cache()
            self._wyckoff_event_store.clear_runtime_cache()
            self._clear_backtest_signal_matrix_runtime_cache()
            self._clear_backtest_input_pool_runtime_cache()
            self._clear_backtest_precheck_cache()
            self._signals_cache.clear()
            self._trim_candles_runtime_cache(target_max_symbols=self._candles_runtime_idle_keep_symbols())
            gc.collect()
        except Exception:
            return

    def _run_backtest_precheck_with_cache(self, payload: BacktestRunRequest) -> None:
        cache_key = self._build_backtest_precheck_cache_key(payload)
        cached = self._load_backtest_precheck_cache(cache_key)
        if cached is not None:
            cached_error_code, cached_error_message = cached
            if cached_error_code and cached_error_message:
                raise BacktestValidationError(cached_error_code, cached_error_message)
            return
        try:
            self._precheck_backtest_data_coverage_before_task(payload)
        except BacktestValidationError as exc:
            self._save_backtest_precheck_cache(
                cache_key,
                error_code=exc.code,
                error_message=str(exc),
            )
            raise
        self._save_backtest_precheck_cache(cache_key, error_code=None, error_message=None)

    def _is_backtest_result_cache_enabled(self) -> bool:
        return self._env_flag("TDX_TREND_BACKTEST_RESULT_CACHE", True)

    @staticmethod
    def _backtest_result_cache_ttl_sec() -> float:
        raw = os.getenv("TDX_TREND_BACKTEST_RESULT_CACHE_TTL_SEC", "").strip()
        if not raw:
            return 48 * 3600.0
        try:
            return max(0.0, float(raw))
        except Exception:
            return 48 * 3600.0

    @staticmethod
    def _is_backtest_result_cache_eligible(payload: BacktestRunRequest) -> bool:
        if payload.mode == "trend_pool" and not str(payload.run_id or "").strip():
            # trend_pool 回测必须显式绑定 run_id，缺失时不做持久缓存。
            return False
        return True

    def _build_backtest_result_cache_key(
        self,
        payload: BacktestRunRequest,
        *,
        strategy_version: str,
        strategy_params_hash: str,
        event_judgment_profile_hash: str,
    ) -> str:
        payload_raw = payload.model_dump(exclude_none=True)
        board_filters = payload_raw.get("board_filters")
        if isinstance(board_filters, list):
            payload_raw["board_filters"] = sorted(
                {str(item).strip().lower() for item in board_filters if str(item).strip()}
            )
        payload_meta = {
            "version": self._BACKTEST_RESULT_CACHE_VERSION,
            "payload": payload_raw,
            "config": {
                "tdx_root": str(self._resolve_user_path(self._config.tdx_data_path)),
                "market_data_source": str(self._config.market_data_source).strip(),
                "candles_window_bars": int(self._config.candles_window_bars),
                "return_window_days": int(self._config.return_window_days),
                "top_n": int(self._config.top_n),
                "full_market_system_protect_limit": int(max(1, int(self._FULL_MARKET_SYSTEM_PROTECT_LIMIT))),
            },
            "algo": {
                "matrix_algo_version": str(self._backtest_matrix_algo_version).strip(),
                "wyckoff_algo_version": str(self._wyckoff_event_algo_version).strip(),
                "wyckoff_data_version": str(self._wyckoff_event_data_version).strip(),
                "matrix_enabled": bool(self._is_backtest_matrix_engine_enabled()),
            },
            "strategy": {
                "strategy_version": str(strategy_version).strip(),
                "strategy_params_hash": str(strategy_params_hash).strip(),
                "event_judgment_profile_hash": str(event_judgment_profile_hash).strip(),
            },
        }
        raw = json.dumps(payload_meta, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:24]

    def _backtest_result_cache_file(self, cache_key: str) -> Path:
        return self._resolve_backtest_result_cache_dir() / f"{cache_key}.json"

    def _load_backtest_result_cache(
        self,
        payload: BacktestRunRequest,
        *,
        strategy_version: str,
        strategy_params_hash: str,
        event_judgment_profile_hash: str,
    ) -> BacktestResponse | None:
        if not self._is_backtest_result_cache_enabled():
            return None
        if not self._is_backtest_result_cache_eligible(payload):
            return None
        cache_key = self._build_backtest_result_cache_key(
            payload,
            strategy_version=strategy_version,
            strategy_params_hash=strategy_params_hash,
            event_judgment_profile_hash=event_judgment_profile_hash,
        )
        path = self._backtest_result_cache_file(cache_key)
        if not path.exists():
            return None
        ttl_sec = self._backtest_result_cache_ttl_sec()
        if ttl_sec > 0:
            try:
                age_sec = max(0.0, time.time() - path.stat().st_mtime)
                if age_sec > ttl_sec:
                    return None
            except Exception:
                return None
        try:
            payload_raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload_raw, dict):
                return None
            result_raw = payload_raw.get("result")
            if not isinstance(result_raw, dict):
                return None
            return BacktestResponse(**result_raw)
        except Exception:
            return None

    def _save_backtest_result_cache(
        self,
        payload: BacktestRunRequest,
        result: BacktestResponse,
        *,
        strategy_version: str,
        strategy_params_hash: str,
        event_judgment_profile_hash: str,
    ) -> bool:
        if not self._is_backtest_result_cache_enabled():
            return False
        if not self._is_backtest_result_cache_eligible(payload):
            return False
        cache_key = self._build_backtest_result_cache_key(
            payload,
            strategy_version=strategy_version,
            strategy_params_hash=strategy_params_hash,
            event_judgment_profile_hash=event_judgment_profile_hash,
        )
        path = self._backtest_result_cache_file(cache_key)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            body = {
                "schema_version": 1,
                "created_at": self._now_datetime(),
                "cache_key": cache_key,
                "result": result.model_dump(exclude_none=True),
            }
            tmp_path = path.with_suffix(".tmp")
            tmp_path.write_text(
                json.dumps(body, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            tmp_path.replace(path)
            return True
        except Exception:
            return False

    @staticmethod
    def _normalize_plateau_axis_int(
        values: list[int],
        *,
        base: int,
        lower: int,
        upper: int,
    ) -> list[int]:
        source = values if values else [int(base)]
        out: list[int] = []
        seen: set[int] = set()
        for raw in source:
            value = int(raw)
            value = max(lower, min(upper, value))
            if value in seen:
                continue
            seen.add(value)
            out.append(value)
        if not out:
            out.append(max(lower, min(upper, int(base))))
        return out

    @staticmethod
    def _normalize_plateau_axis_float(
        values: list[float],
        *,
        base: float,
        lower: float,
        upper: float,
        precision: int = 6,
    ) -> list[float]:
        source = values if values else [float(base)]
        out: list[float] = []
        seen: set[float] = set()
        for raw in source:
            value = float(raw)
            value = max(lower, min(upper, value))
            value = round(value, precision)
            if value in seen:
                continue
            seen.add(value)
            out.append(value)
        if not out:
            out.append(round(max(lower, min(upper, float(base))), precision))
        return out

    @staticmethod
    def _backtest_plateau_score(result: BacktestResponse) -> float:
        total_return = float(result.stats.total_return)
        max_drawdown = abs(min(0.0, float(result.stats.max_drawdown)))
        win_rate = max(0.0, min(1.0, float(result.stats.win_rate)))
        trade_count = max(0, int(result.stats.trade_count))
        profit_factor = float(result.stats.profit_factor)
        if not math.isfinite(profit_factor):
            profit_factor = 5.0
        profit_factor = max(0.0, min(profit_factor, 5.0))
        monthly_returns = [
            float(row.return_ratio)
            for row in result.monthly_returns
            if math.isfinite(float(row.return_ratio))
        ]
        monthly_std = float(np.std(monthly_returns)) if len(monthly_returns) >= 2 else 0.0
        # Penalize low win-rate parameter sets so "small loss / large gain" tails do not dominate ranking.
        win_rate_term = (win_rate - 0.5) * 0.2
        low_win_penalty = max(0.0, 0.45 - win_rate) * 0.9
        low_trade_penalty = max(0.0, float(20 - trade_count) / 20.0) * 0.25 if trade_count < 20 else 0.0
        variance_penalty = min(0.25, max(0.0, monthly_std) * 2.5)
        return round(
            total_return
            - max_drawdown * 0.6
            + profit_factor * 0.02
            + win_rate_term
            - low_win_penalty
            - low_trade_penalty
            - variance_penalty,
            6,
        )

    @staticmethod
    def _plateau_param_vector(point: BacktestPlateauPoint) -> tuple[float, ...]:
        return (
            float(point.params.window_days),
            float(point.params.min_score),
            float(point.params.stop_loss),
            float(point.params.take_profit),
            float(point.params.trailing_stop_pct),
            float(point.params.intraday_trailing_reduce_ratio),
            float(point.params.max_positions),
            float(point.params.position_pct),
            float(point.params.max_symbols),
            float(point.params.priority_topk_per_day),
        )

    @staticmethod
    def _plateau_param_dim_weights() -> tuple[float, ...]:
        return (
            1.0,  # window_days
            1.0,  # min_score
            0.8,  # stop_loss
            0.8,  # take_profit
            0.8,  # trailing_stop_pct
            0.5,  # intraday_trailing_reduce_ratio
            0.5,  # max_positions
            0.5,  # position_pct
            0.5,  # max_symbols
            0.5,  # priority_topk_per_day
        )

    @staticmethod
    def _safe_percentile_scores(values: list[float]) -> list[float]:
        if not values:
            return []
        if len(values) == 1:
            return [50.0]
        sorted_indices = sorted(range(len(values)), key=lambda idx: float(values[idx]))
        out = [50.0] * len(values)
        cursor = 0
        while cursor < len(sorted_indices):
            end = cursor
            base_value = float(values[sorted_indices[cursor]])
            while end + 1 < len(sorted_indices) and float(values[sorted_indices[end + 1]]) == base_value:
                end += 1
            rank = ((cursor + end) / 2.0) / float(len(values) - 1) * 100.0
            for pos in range(cursor, end + 1):
                out[sorted_indices[pos]] = round(float(rank), 6)
            cursor = end + 1
        return out

    @staticmethod
    def _estimate_backtest_years(date_from: str, date_to: str) -> float:
        try:
            start = datetime.strptime(str(date_from), "%Y-%m-%d").date()
            end = datetime.strptime(str(date_to), "%Y-%m-%d").date()
        except Exception:
            return 1.0
        delta_days = max(1, (end - start).days + 1)
        return max(1.0 / 12.0, float(delta_days) / 365.25)

    @classmethod
    def _format_plateau_param_range(cls, param_name: str, values: list[float]) -> str:
        if not values:
            return "--"
        lower = min(float(item) for item in values)
        upper = max(float(item) for item in values)
        if param_name in {"window_days", "max_positions", "max_symbols", "priority_topk_per_day"}:
            lower_text = str(int(round(lower)))
            upper_text = str(int(round(upper)))
        elif param_name == "min_score":
            lower_text = f"{lower:.2f}"
            upper_text = f"{upper:.2f}"
        else:
            lower_text = f"{lower * 100.0:.2f}%"
            upper_text = f"{upper * 100.0:.2f}%"
        if lower_text == upper_text:
            return lower_text
        return f"{lower_text} ~ {upper_text}"

    def _apply_plateau_robustness_scores(
        self,
        points: list[BacktestPlateauPoint],
        *,
        base_payload: BacktestRunRequest,
    ) -> tuple[list[BacktestPlateauPoint], list[dict[str, Any]]]:
        valid_points = [row for row in points if row.error is None]
        if not valid_points:
            return [], []

        backtest_years = self._estimate_backtest_years(base_payload.date_from, base_payload.date_to)
        min_avg_pnl_ratio = max(0.002, float(base_payload.fee_bps) * 4.0 / 10000.0)

        calmar_values: list[float] = []
        return_values: list[float] = []
        profit_factor_values: list[float] = []
        annual_trade_values: list[float] = []
        fill_rate_values: list[float] = []

        for row in valid_points:
            annual_trades = float(row.stats.trade_count) / backtest_years
            row.annual_trades = round(float(annual_trades), 6)
            failures: list[str] = []
            if float(row.stats.total_return) <= 0.0:
                failures.append("收益<=0")
            if float(row.stats.profit_factor) < 1.05:
                failures.append("ProfitFactor<1.05")
            if abs(float(row.stats.max_drawdown)) > 0.35:
                failures.append("回撤>35%")
            if float(row.fill_rate) < 0.10:
                failures.append("fill_rate<10%")
            if annual_trades < 12.0:
                failures.append("年化交易数<12")
            if float(row.stats.avg_pnl_ratio) < min_avg_pnl_ratio:
                failures.append("单笔收益不足覆盖成本")
            row.hard_filter_failures = failures
            row.passes_hard_filters = len(failures) == 0

            max_drawdown = max(abs(float(row.stats.max_drawdown)), 0.02)
            calmar_values.append(float(row.stats.total_return) / max_drawdown)
            return_values.append(float(row.stats.total_return))
            profit_factor = float(row.stats.profit_factor)
            if not math.isfinite(profit_factor):
                profit_factor = 5.0
            profit_factor_values.append(max(0.0, min(profit_factor, 5.0)))
            annual_trade_values.append(float(annual_trades))
            fill_rate_values.append(float(row.fill_rate))

        calmar_percentiles = self._safe_percentile_scores(calmar_values)
        return_percentiles = self._safe_percentile_scores(return_values)
        profit_factor_percentiles = self._safe_percentile_scores(profit_factor_values)
        annual_trade_percentiles = self._safe_percentile_scores(annual_trade_values)
        fill_rate_percentiles = self._safe_percentile_scores(fill_rate_values)

        raw_vectors = [self._plateau_param_vector(row) for row in valid_points]
        dim_count = len(raw_vectors[0])
        minimums: list[float] = []
        spans: list[float] = []
        for dim in range(dim_count):
            values = [vector[dim] for vector in raw_vectors]
            lower = min(values)
            span = max(values) - lower
            minimums.append(lower)
            spans.append(span if span > 1e-9 else 1.0)
        normalized_vectors: list[tuple[float, ...]] = []
        for vector in raw_vectors:
            normalized_vectors.append(
                tuple(
                    (float(vector[dim]) - minimums[dim]) / spans[dim]
                    for dim in range(dim_count)
                )
            )

        dim_weights = self._plateau_param_dim_weights()
        neighbor_count = min(12, max(0, len(valid_points) - 1))
        neighbor_meta: list[dict[str, Any]] = []
        raw_sensitivity_values: list[float] = []

        for idx, row in enumerate(valid_points):
            row.point_score = round(
                (
                    0.30 * calmar_percentiles[idx]
                    + 0.25 * return_percentiles[idx]
                    + 0.15 * profit_factor_percentiles[idx]
                    + 0.15 * annual_trade_percentiles[idx]
                    + 0.15 * fill_rate_percentiles[idx]
                ),
                6,
            )
            distance_items: list[tuple[float, int]] = []
            for other_idx, other_vector in enumerate(normalized_vectors):
                if other_idx == idx:
                    continue
                diff_sq = 0.0
                for dim in range(dim_count):
                    diff = float(normalized_vectors[idx][dim]) - float(other_vector[dim])
                    diff_sq += float(dim_weights[dim]) * diff * diff
                distance_items.append((math.sqrt(diff_sq), other_idx))
            distance_items.sort(key=lambda item: (item[0], item[1]))
            neighbors = distance_items[:neighbor_count]
            if neighbors:
                neighbor_points = [valid_points[item[1]] for item in neighbors]
                pass_rate = sum(1 for item in neighbor_points if item.passes_hard_filters) / float(len(neighbor_points))
                neighbor_point_scores = [float(item.point_score) for item in neighbor_points]
                neighbor_median = self._quantile(neighbor_point_scores, 0.50)
                neighbor_p25 = self._quantile(neighbor_point_scores, 0.25)
                raw_sensitivity = self._safe_mean(
                    [
                        abs(float(valid_points[item[1]].point_score) - float(row.point_score)) / max(0.05, float(item[0]))
                        for item in neighbors
                    ]
                )
            else:
                pass_rate = 1.0 if row.passes_hard_filters else 0.0
                neighbor_median = float(row.point_score)
                neighbor_p25 = float(row.point_score)
                raw_sensitivity = 0.0
            row.neighbor_pass_rate = round(float(pass_rate), 6)
            row.neighbor_median_score = round(float(neighbor_median), 6)
            row.neighbor_p25_score = round(float(neighbor_p25), 6)
            neighbor_meta.append(
                {
                    "vector": normalized_vectors[idx],
                    "neighbors": neighbors,
                }
            )
            raw_sensitivity_values.append(float(raw_sensitivity))

        sensitivity_percentiles = self._safe_percentile_scores(raw_sensitivity_values)
        for idx, row in enumerate(valid_points):
            stability_score = max(0.0, 100.0 - float(sensitivity_percentiles[idx]))
            row.sensitivity_penalty = round(float(sensitivity_percentiles[idx]), 6)
            row.local_score = round(
                (
                    0.35 * float(row.neighbor_pass_rate) * 100.0
                    + 0.30 * float(row.neighbor_median_score)
                    + 0.20 * float(row.neighbor_p25_score)
                    + 0.15 * stability_score
                ),
                6,
            )
            combined_score = 0.35 * float(row.point_score) + 0.65 * float(row.local_score)
            if not row.passes_hard_filters:
                combined_score *= 0.45
            row.plateau_score = round(float(combined_score), 6)
            neighbor_meta[idx]["stability_score"] = round(float(stability_score), 6)

        return valid_points, neighbor_meta

    def _build_backtest_plateau_run_payload(
        self,
        base_payload: BacktestRunRequest,
        params: BacktestPlateauParams,
    ) -> BacktestRunRequest:
        return base_payload.model_copy(
            update={
                "window_days": params.window_days,
                "min_score": params.min_score,
                "stop_loss": params.stop_loss,
                "take_profit": params.take_profit,
                "trailing_stop_pct": params.trailing_stop_pct,
                "intraday_trailing_reduce_ratio": params.intraday_trailing_reduce_ratio,
                "max_positions": params.max_positions,
                "position_pct": params.position_pct,
                "max_symbols": params.max_symbols,
                "priority_topk_per_day": params.priority_topk_per_day,
                "enable_advanced_analysis": False,
            },
            deep=True,
        )

    def _build_backtest_plateau_regions(
        self,
        valid_points: list[BacktestPlateauPoint],
        neighbor_meta: list[dict[str, Any]],
        *,
        base_payload: BacktestRunRequest,
        control_callback: Callable[[], None] | None = None,
        include_oos: bool = True,
    ) -> list[BacktestPlateauRegionSummary]:
        if not valid_points:
            return []

        candidate_indices = [
            idx
            for idx, row in enumerate(valid_points)
            if row.passes_hard_filters and float(row.local_score) >= 55.0
        ]
        if not candidate_indices:
            return []

        candidate_set = set(candidate_indices)
        connect_neighbor_count = min(4, max(1, min(12, len(valid_points) - 1)))
        adjacency: dict[int, set[int]] = {idx: set() for idx in candidate_indices}
        for idx in candidate_indices:
            for _distance, neighbor_idx in neighbor_meta[idx]["neighbors"][:connect_neighbor_count]:
                if neighbor_idx in candidate_set:
                    adjacency[idx].add(int(neighbor_idx))
                    adjacency[int(neighbor_idx)].add(idx)

        components: list[list[int]] = []
        visited: set[int] = set()
        for idx in candidate_indices:
            if idx in visited:
                continue
            stack = [idx]
            component: list[int] = []
            visited.add(idx)
            while stack:
                current = stack.pop()
                component.append(current)
                for neighbor_idx in adjacency.get(current, set()):
                    if neighbor_idx in visited:
                        continue
                    visited.add(neighbor_idx)
                    stack.append(neighbor_idx)
            components.append(sorted(component))

        provisional_regions: list[dict[str, Any]] = []
        max_candidate_count = max(1, len(candidate_indices))
        for comp_index, component in enumerate(components, start=1):
            members = [valid_points[idx] for idx in component]
            vectors = [neighbor_meta[idx]["vector"] for idx in component]
            avg_distances: list[float] = []
            for vector in vectors:
                if len(vectors) <= 1:
                    avg_distances.append(0.0)
                    continue
                distances: list[float] = []
                for other in vectors:
                    if other is vector:
                        continue
                    diff_sq = sum((float(vector[dim]) - float(other[dim])) ** 2 for dim in range(len(vector)))
                    distances.append(math.sqrt(diff_sq))
                avg_distances.append(self._safe_mean(distances))
            centrality_percentiles = self._safe_percentile_scores(avg_distances)
            center_scores: list[float] = []
            for idx, member in enumerate(members):
                center_scores.append(
                    0.70 * float(member.plateau_score)
                    + 0.30 * (100.0 - float(centrality_percentiles[idx]))
                )
            center_local_idx = max(
                range(len(members)),
                key=lambda item: (
                    center_scores[item],
                    members[item].local_score,
                    members[item].plateau_score,
                    members[item].stats.total_return,
                ),
            )
            center_point = members[center_local_idx]
            center_margin_score = round(float(100.0 - centrality_percentiles[center_local_idx]), 6)
            size_score = round(min(100.0, float(len(component)) / 6.0 * 100.0), 6)
            median_local = round(self._quantile([float(item.local_score) for item in members], 0.50), 6)
            p25_local = round(self._quantile([float(item.local_score) for item in members], 0.25), 6)
            median_point = round(self._quantile([float(item.point_score) for item in members], 0.50), 6)
            median_total_return = round(self._quantile([float(item.stats.total_return) for item in members], 0.50), 6)
            best_total_return = round(max(float(item.stats.total_return) for item in members), 6)
            provisional_score = round(
                0.45 * median_local
                + 0.25 * p25_local
                + 0.15 * size_score
                + 0.15 * center_margin_score,
                6,
            )
            parameter_ranges = {
                "window_days": self._format_plateau_param_range("window_days", [float(item.params.window_days) for item in members]),
                "min_score": self._format_plateau_param_range("min_score", [float(item.params.min_score) for item in members]),
                "stop_loss": self._format_plateau_param_range("stop_loss", [float(item.params.stop_loss) for item in members]),
                "take_profit": self._format_plateau_param_range("take_profit", [float(item.params.take_profit) for item in members]),
                "trailing_stop_pct": self._format_plateau_param_range("trailing_stop_pct", [float(item.params.trailing_stop_pct) for item in members]),
                "intraday_trailing_reduce_ratio": self._format_plateau_param_range(
                    "intraday_trailing_reduce_ratio",
                    [float(item.params.intraday_trailing_reduce_ratio) for item in members],
                ),
                "max_positions": self._format_plateau_param_range("max_positions", [float(item.params.max_positions) for item in members]),
                "position_pct": self._format_plateau_param_range("position_pct", [float(item.params.position_pct) for item in members]),
                "max_symbols": self._format_plateau_param_range("max_symbols", [float(item.params.max_symbols) for item in members]),
                "priority_topk_per_day": self._format_plateau_param_range("priority_topk_per_day", [float(item.params.priority_topk_per_day) for item in members]),
            }
            provisional_regions.append(
                {
                    "component": component,
                    "internal_id": f"region_{comp_index}",
                    "center_point": center_point,
                    "median_local_score": median_local,
                    "p25_local_score": p25_local,
                    "median_point_score": median_point,
                    "median_total_return": median_total_return,
                    "best_total_return": best_total_return,
                    "center_margin_score": center_margin_score,
                    "size_score": size_score,
                    "parameter_ranges": parameter_ranges,
                    "point_count": len(component),
                    "region_score": provisional_score,
                }
            )

        provisional_regions.sort(
            key=lambda item: (
                float(item["region_score"]),
                int(item["point_count"]),
                float(item["median_local_score"]),
                float(item["best_total_return"]),
            ),
            reverse=True,
        )

        if include_oos:
            for region in provisional_regions[:3]:
                if control_callback is not None:
                    control_callback()
                center_payload = self._build_backtest_plateau_run_payload(base_payload, region["center_point"].params)
                walk_forward = self._run_backtest_walk_forward(center_payload, control_callback=control_callback)
                region["walk_forward"] = walk_forward
                if int(walk_forward.fold_count) > 0:
                    region["oos_pass_rate"] = round(float(walk_forward.oos_pass_rate) * 100.0, 6)
                else:
                    region["oos_pass_rate"] = None

        for region in provisional_regions:
            oos_pass_rate = region.get("oos_pass_rate")
            weighted_terms = [
                (0.30, float(region["median_local_score"])),
                (0.20, float(region["p25_local_score"])),
                (0.15, float(region["size_score"])),
                (0.15, float(region["center_margin_score"])),
            ]
            if oos_pass_rate is not None:
                weighted_terms.append((0.20, float(oos_pass_rate)))
            weight_sum = sum(weight for weight, _value in weighted_terms)
            final_score = sum(weight * value for weight, value in weighted_terms) / max(weight_sum, 1e-9)
            region["region_score"] = round(float(final_score), 6)

        provisional_regions.sort(
            key=lambda item: (
                float(item["region_score"]),
                int(item["point_count"]),
                float(item["median_local_score"]),
                float(item["best_total_return"]),
            ),
            reverse=True,
        )

        output_regions: list[BacktestPlateauRegionSummary] = []
        for rank, region in enumerate(provisional_regions, start=1):
            region_id = f"region_{rank}"
            for point_idx in region["component"]:
                valid_points[point_idx].region_id = region_id
                valid_points[point_idx].region_rank = int(rank)
            output_regions.append(
                BacktestPlateauRegionSummary(
                    region_id=region_id,
                    region_rank=int(rank),
                    point_count=int(region["point_count"]),
                    parameter_ranges=dict(region["parameter_ranges"]),
                    center_point=region["center_point"],
                    median_local_score=float(region["median_local_score"]),
                    p25_local_score=float(region["p25_local_score"]),
                    median_point_score=float(region["median_point_score"]),
                    median_total_return=float(region["median_total_return"]),
                    best_total_return=float(region["best_total_return"]),
                    center_margin_score=float(region["center_margin_score"]),
                    size_score=float(region["size_score"]),
                    oos_pass_rate=(
                        None
                        if region.get("oos_pass_rate") is None
                        else float(region["oos_pass_rate"])
                    ),
                    region_score=float(region["region_score"]),
                    walk_forward=region.get("walk_forward"),
                )
            )

        return output_regions

    @staticmethod
    def _select_backtest_plateau_recommendation(regions):
        """A ranked point is not a recommendation without an eligible region."""
        for region in regions:
            point = region.center_point
            if point.error is None and point.passes_hard_filters and point.stats.total_return > 0:
                return point
        return None

    def _rehydrate_backtest_plateau_result(
        self,
        result: BacktestPlateauResponse,
        *,
        control_callback: Callable[[], None] | None = None,
    ) -> BacktestPlateauResponse:
        version = re.match(r'^(\d+)\.(\d+)\.(\d+)', result.strategy_version or '')
        legacy_rhythm = result.base_payload.strategy_id == 'ths_force_rhythm_v1' and (
            not version or tuple(map(int, version.groups())) < (1, 0, 1))
        if legacy_rhythm:
            warning = '旧版节奏波实现存在买点错绑，请使用当前策略重新回测。'
            return result.model_copy(update={"recommended_point": None, "best_point": None, "regions": [],
                                             "notes": list(dict.fromkeys([warning, *result.notes]))}, deep=True)
        points = [item.model_copy(deep=True) for item in list(result.points or [])]
        valid_points = [item for item in points if item.error is None]
        needs_refresh = (
            result.peak_point is None
            or any(
                (float(item.plateau_score) == 0.0 and float(item.local_score) == 0.0 and float(item.point_score) == 0.0)
                for item in valid_points
            )
        )
        if not needs_refresh:
            recommended = self._select_backtest_plateau_recommendation(result.regions)
            return result.model_copy(update={"recommended_point": recommended, "best_point": recommended}, deep=True)

        scored_points, neighbor_meta = self._apply_plateau_robustness_scores(
            points,
            base_payload=result.base_payload,
        )
        regions = self._build_backtest_plateau_regions(
            scored_points,
            neighbor_meta,
            base_payload=result.base_payload,
            control_callback=control_callback,
            include_oos=False,
        )
        points.sort(
            key=lambda row: (
                row.error is None,
                row.plateau_score,
                row.local_score,
                row.point_score,
                row.score,
                row.stats.total_return,
                row.stats.win_rate,
            ),
            reverse=True,
        )
        recommended_point = self._select_backtest_plateau_recommendation(regions)
        peak_point = None
        if scored_points:
            peak_point = max(
                scored_points,
                key=lambda row: (
                    row.score,
                    row.stats.total_return,
                    row.stats.win_rate,
                ),
            )
        return result.model_copy(
            update={
                "points": points,
                "best_point": recommended_point,
                "recommended_point": recommended_point,
                "peak_point": peak_point,
                "regions": regions,
            },
            deep=True,
        )

    @staticmethod
    def _safe_pearson_correlation(x_values: list[float], y_values: list[float]) -> float:
        count = min(len(x_values), len(y_values))
        if count < 2:
            return 0.0
        xs = [float(x_values[idx]) for idx in range(count)]
        ys = [float(y_values[idx]) for idx in range(count)]
        mean_x = sum(xs) / float(count)
        mean_y = sum(ys) / float(count)
        var_x = sum((value - mean_x) ** 2 for value in xs)
        var_y = sum((value - mean_y) ** 2 for value in ys)
        if var_x <= 1e-12 or var_y <= 1e-12:
            return 0.0
        cov = sum((xs[idx] - mean_x) * (ys[idx] - mean_y) for idx in range(count))
        corr = cov / math.sqrt(var_x * var_y)
        if not math.isfinite(corr):
            return 0.0
        return max(-1.0, min(1.0, float(corr)))

    def _build_backtest_plateau_correlations(
        self,
        points: list[BacktestPlateauPoint],
    ) -> list[BacktestPlateauCorrelationRow]:
        valid_points = [row for row in points if row.error is None]
        if len(valid_points) < 2:
            return []

        score_values = [float(row.score) for row in valid_points]
        total_return_values = [float(row.stats.total_return) for row in valid_points]
        win_rate_values = [float(row.stats.win_rate) for row in valid_points]
        parameter_extractors: list[tuple[str, str, Callable[[BacktestPlateauPoint], float]]] = [
            ("window_days", "信号窗口天数", lambda row: float(row.params.window_days)),
            ("min_score", "最低评分", lambda row: float(row.params.min_score)),
            ("stop_loss", "止损比例", lambda row: float(row.params.stop_loss)),
            ("take_profit", "止盈比例", lambda row: float(row.params.take_profit)),
            ("trailing_stop_pct", "高位回撤比例", lambda row: float(row.params.trailing_stop_pct)),
            ("intraday_trailing_reduce_ratio", "日内减仓比例", lambda row: float(row.params.intraday_trailing_reduce_ratio)),
            ("max_positions", "最大并发持仓", lambda row: float(row.params.max_positions)),
            ("position_pct", "单笔仓位占比", lambda row: float(row.params.position_pct)),
            ("max_symbols", "最大股票数", lambda row: float(row.params.max_symbols)),
            ("priority_topk_per_day", "同日TopK", lambda row: float(row.params.priority_topk_per_day)),
        ]
        rows: list[BacktestPlateauCorrelationRow] = []
        for key, label, extractor in parameter_extractors:
            x_values = [extractor(row) for row in valid_points]
            rows.append(
                BacktestPlateauCorrelationRow(
                    parameter=key,
                    parameter_label=label,
                    score_corr=round(self._safe_pearson_correlation(x_values, score_values), 6),
                    total_return_corr=round(self._safe_pearson_correlation(x_values, total_return_values), 6),
                    win_rate_corr=round(self._safe_pearson_correlation(x_values, win_rate_values), 6),
                )
            )
        rows.sort(
            key=lambda row: max(
                abs(float(row.score_corr)),
                abs(float(row.total_return_corr)),
                abs(float(row.win_rate_corr)),
            ),
            reverse=True,
        )
        return rows

    @staticmethod
    def _build_plateau_correlation_summary_note(
        correlations: list[BacktestPlateauCorrelationRow],
        *,
        metric_label: str,
        corr_field: Literal["score_corr", "total_return_corr", "win_rate_corr"],
    ) -> str | None:
        if len(correlations) <= 0:
            return None
        positive = max(correlations, key=lambda row: float(getattr(row, corr_field)))
        negative = min(correlations, key=lambda row: float(getattr(row, corr_field)))
        positive_value = float(getattr(positive, corr_field))
        negative_value = float(getattr(negative, corr_field))
        parts: list[str] = []
        if positive_value > 0.05:
            parts.append(f"{positive.parameter_label}正相关 {positive_value:+.3f}")
        if negative_value < -0.05 and negative.parameter != positive.parameter:
            parts.append(f"{negative.parameter_label}负相关 {negative_value:+.3f}")
        if len(parts) <= 0:
            return None
        return f"{metric_label}相关性：{'；'.join(parts)}。"

    @staticmethod
    def _resolve_plateau_sample_points(payload: BacktestPlateauRunRequest) -> int:
        if payload.sample_points is not None:
            return max(1, int(payload.sample_points))
        return max(1, int(payload.max_points))

    def _backtest_plateau_eval_workers(self) -> int:
        configured_default_raw = getattr(self._config, "backtest_plateau_workers", 4)
        try:
            configured_default = int(configured_default_raw)
        except Exception:
            configured_default = 4
        configured_default = max(1, min(32, configured_default))

        raw = os.getenv("TDX_TREND_BACKTEST_PLATEAU_WORKERS", "").strip()
        if raw:
            try:
                return max(1, min(32, int(raw)))
            except Exception:
                return configured_default
        return configured_default

    @staticmethod
    def _lhs_unit_matrix(point_count: int, dim_count: int, rng: random.Random) -> list[list[float]]:
        if point_count <= 0:
            return []
        matrix: list[list[float]] = [[0.0 for _ in range(dim_count)] for _ in range(point_count)]
        for dim in range(dim_count):
            bins = list(range(point_count))
            rng.shuffle(bins)
            for row in range(point_count):
                matrix[row][dim] = (float(bins[row]) + rng.random()) / float(point_count)
        return matrix

    @staticmethod
    def _map_plateau_int_from_unit(
        unit_value: float,
        *,
        lower: int,
        upper: int,
    ) -> int:
        if upper <= lower:
            return int(lower)
        mapped = int(round(float(lower) + float(unit_value) * float(upper - lower)))
        return max(int(lower), min(int(upper), mapped))

    @staticmethod
    def _map_plateau_float_from_unit(
        unit_value: float,
        *,
        lower: float,
        upper: float,
        precision: int,
    ) -> float:
        if upper <= lower:
            return round(float(lower), int(precision))
        mapped = float(lower) + float(unit_value) * float(upper - lower)
        mapped = max(float(lower), min(float(upper), mapped))
        return round(mapped, int(precision))

    def _build_plateau_lhs_params(
        self,
        *,
        point_count: int,
        random_seed: int | None,
        window_axis: list[int],
        min_score_axis: list[float],
        stop_loss_axis: list[float],
        take_profit_axis: list[float],
        trailing_stop_axis: list[float],
        intraday_reduce_axis: list[float],
        max_positions_axis: list[int],
        position_pct_axis: list[float],
        max_symbols_axis: list[int],
        priority_topk_axis: list[int],
    ) -> list[BacktestPlateauParams]:
        if point_count <= 0:
            return []

        rng = random.Random(int(random_seed)) if random_seed is not None else random.Random()
        window_bounds = (min(window_axis), max(window_axis))
        min_score_bounds = (min(min_score_axis), max(min_score_axis))
        stop_loss_bounds = (min(stop_loss_axis), max(stop_loss_axis))
        take_profit_bounds = (min(take_profit_axis), max(take_profit_axis))
        trailing_stop_bounds = (min(trailing_stop_axis), max(trailing_stop_axis))
        intraday_reduce_bounds = (min(intraday_reduce_axis), max(intraday_reduce_axis))
        max_positions_bounds = (min(max_positions_axis), max(max_positions_axis))
        position_pct_bounds = (min(position_pct_axis), max(position_pct_axis))
        max_symbols_bounds = (min(max_symbols_axis), max(max_symbols_axis))
        priority_topk_bounds = (min(priority_topk_axis), max(priority_topk_axis))

        seen: set[tuple[object, ...]] = set()
        params: list[BacktestPlateauParams] = []

        def _try_append(unit_values: list[float]) -> None:
            candidate = BacktestPlateauParams(
                window_days=self._map_plateau_int_from_unit(
                    unit_values[0],
                    lower=int(window_bounds[0]),
                    upper=int(window_bounds[1]),
                ),
                min_score=self._map_plateau_float_from_unit(
                    unit_values[1],
                    lower=float(min_score_bounds[0]),
                    upper=float(min_score_bounds[1]),
                    precision=4,
                ),
                stop_loss=self._map_plateau_float_from_unit(
                    unit_values[2],
                    lower=float(stop_loss_bounds[0]),
                    upper=float(stop_loss_bounds[1]),
                    precision=6,
                ),
                take_profit=self._map_plateau_float_from_unit(
                    unit_values[3],
                    lower=float(take_profit_bounds[0]),
                    upper=float(take_profit_bounds[1]),
                    precision=6,
                ),
                trailing_stop_pct=self._map_plateau_float_from_unit(
                    unit_values[4],
                    lower=float(trailing_stop_bounds[0]),
                    upper=float(trailing_stop_bounds[1]),
                    precision=6,
                ),
                intraday_trailing_reduce_ratio=self._map_plateau_float_from_unit(
                    unit_values[5],
                    lower=float(intraday_reduce_bounds[0]),
                    upper=float(intraday_reduce_bounds[1]),
                    precision=6,
                ),
                max_positions=self._map_plateau_int_from_unit(
                    unit_values[6],
                    lower=int(max_positions_bounds[0]),
                    upper=int(max_positions_bounds[1]),
                ),
                position_pct=self._map_plateau_float_from_unit(
                    unit_values[7],
                    lower=float(position_pct_bounds[0]),
                    upper=float(position_pct_bounds[1]),
                    precision=6,
                ),
                max_symbols=self._map_plateau_int_from_unit(
                    unit_values[8],
                    lower=int(max_symbols_bounds[0]),
                    upper=int(max_symbols_bounds[1]),
                ),
                priority_topk_per_day=self._map_plateau_int_from_unit(
                    unit_values[9],
                    lower=int(priority_topk_bounds[0]),
                    upper=int(priority_topk_bounds[1]),
                ),
            )
            key = (
                int(candidate.window_days),
                float(candidate.min_score),
                float(candidate.stop_loss),
                float(candidate.take_profit),
                float(candidate.trailing_stop_pct),
                float(candidate.intraday_trailing_reduce_ratio),
                int(candidate.max_positions),
                float(candidate.position_pct),
                int(candidate.max_symbols),
                int(candidate.priority_topk_per_day),
            )
            if key in seen:
                return
            seen.add(key)
            params.append(candidate)

        lhs_units = self._lhs_unit_matrix(point_count, 10, rng)
        for row in lhs_units:
            _try_append(row)
            if len(params) >= point_count:
                return params

        max_attempts = max(512, point_count * 64)
        attempts = 0
        while len(params) < point_count and attempts < max_attempts:
            attempts += 1
            _try_append([rng.random() for _ in range(10)])

        return params[:point_count]

    def _build_backtest_universe_for_plateau(
        self,
        payload: BacktestRunRequest,
        board_filters: list[BoardFilter],
        *,
        control_callback: Callable[[], None] | None = None,
    ) -> tuple[list[str], dict[str, set[str]] | None, list[str]]:
        """Build rolling universe for a backtest payload, returning (symbols, allowed_by_date, notes).

        Used by run_backtest_plateau to pre-build the universe once per unique
        max_symbols value, avoiding redundant TDX loading and screener filtering.
        """
        scan_dates = self._build_backtest_scan_dates(payload.date_from, payload.date_to)
        if not scan_dates:
            raise ValueError("回测区间内无可扫描交易日。")

        if payload.mode == "trend_pool":
            trend_pool_run = self._require_backtest_trend_pool_run(payload.run_id)
            trend_pool_params = self._bind_step_configs_to_screener_params(
                trend_pool_run.params,
                trend_pool_run.step_configs,
            )
            if payload.pool_roll_mode == "position":
                engine = BacktestEngine(
                    get_candles=self._ensure_candles,
                    build_row=self._build_row_from_candles,
                    calc_snapshot=lambda row, window_days, as_of_date: self._calc_wyckoff_snapshot(
                        row, window_days=window_days, as_of_date=as_of_date,
                    ),
                    resolve_symbol_name=self._resolve_symbol_name,
                    strategy_signal_filter=lambda strategy_id, row, snapshot, params: self._strategy_registry.generate_signals(
                        strategy_id=strategy_id,
                        row=row,
                        snapshot=snapshot,
                        params=params,
                    ),
                    strategy_signal_context_builder=lambda strategy_id, snapshot, params: self._build_strategy_signal_context(
                        strategy_id,
                        snapshot,
                        params,
                    ),
                )
                seed_symbols, seed_allowed, _, _, _ = self._build_trend_pool_rolling_universe(
                    payload=payload, screener_params=trend_pool_params,
                    board_filters=board_filters, refresh_dates=[scan_dates[0]],
                    progress_callback=None,
                )
                if not seed_symbols:
                    raise ValueError("回测股票池为空：持仓触发滚动初始池为空。")
                probe = engine.run(
                    payload=payload, symbols=seed_symbols,
                    allowed_symbols_by_date=seed_allowed,
                    control_callback=control_callback,
                )
                refresh_set = self._collect_position_mode_refresh_dates(
                    scan_dates,
                    seed_date=scan_dates[0],
                    exit_dates=[trade.exit_date for trade in probe.trades],
                )
                symbols, allowed, notes, _, rdu = self._build_trend_pool_rolling_universe(
                    payload=payload, screener_params=trend_pool_params,
                    board_filters=board_filters, refresh_dates=refresh_set,
                    progress_callback=None,
                )
                notes.append(f"持仓触发滚动：首日+卖出信号日/卖出后下一交易日刷新，共 {len(rdu)} 次。")
            else:
                symbols, allowed, notes, _, _ = self._build_trend_pool_rolling_universe(
                    payload=payload, screener_params=trend_pool_params,
                    board_filters=board_filters, refresh_dates=None,
                    progress_callback=None,
                )
            if not symbols:
                raise ValueError(f"回测股票池为空：{'；'.join(notes) if notes else '滚动筛选结果为空。'}")
            return symbols, allowed, notes

        if payload.mode == "full_market":
            if payload.pool_roll_mode == "position":
                engine = BacktestEngine(
                    get_candles=self._ensure_candles,
                    build_row=self._build_row_from_candles,
                    calc_snapshot=lambda row, window_days, as_of_date: self._calc_wyckoff_snapshot(
                        row, window_days=window_days, as_of_date=as_of_date,
                    ),
                    resolve_symbol_name=self._resolve_symbol_name,
                    strategy_signal_filter=lambda strategy_id, row, snapshot, params: self._strategy_registry.generate_signals(
                        strategy_id=strategy_id,
                        row=row,
                        snapshot=snapshot,
                        params=params,
                    ),
                    strategy_signal_context_builder=lambda strategy_id, snapshot, params: self._build_strategy_signal_context(
                        strategy_id,
                        snapshot,
                        params,
                    ),
                )
                seed_symbols, seed_allowed, _, _, _ = self._build_full_market_rolling_universe(
                    payload=payload, board_filters=board_filters,
                    refresh_dates=[scan_dates[0]], progress_callback=None,
                )
                if not seed_symbols:
                    raise ValueError("回测股票池为空：持仓触发滚动初始池为空。")
                probe = engine.run(
                    payload=payload, symbols=seed_symbols,
                    allowed_symbols_by_date=seed_allowed,
                    control_callback=control_callback,
                )
                refresh_set = self._collect_position_mode_refresh_dates(
                    scan_dates,
                    seed_date=scan_dates[0],
                    exit_dates=[trade.exit_date for trade in probe.trades],
                )
                symbols, allowed, notes, _, rdu = self._build_full_market_rolling_universe(
                    payload=payload, board_filters=board_filters,
                    refresh_dates=refresh_set, progress_callback=None,
                )
                notes.append(f"持仓触发滚动：首日+卖出信号日/卖出后下一交易日刷新，共 {len(rdu)} 次。")
            else:
                symbols, allowed, notes, _, _ = self._build_full_market_rolling_universe(
                    payload=payload, board_filters=board_filters,
                    refresh_dates=None, progress_callback=None,
                )
            if not symbols:
                raise ValueError(f"回测股票池为空：{'；'.join(notes) if notes else '滚动筛选结果为空。'}")
            return symbols, allowed, notes

        raise ValueError(f"不支持的回测模式: {payload.mode}")

    def run_backtest_plateau(
        self,
        payload: BacktestPlateauRunRequest,
        *,
        task_id: str | None = None,
        progress_callback: Callable[[int, int, str], None] | None = None,
        control_callback: Callable[[], None] | None = None,
    ) -> BacktestPlateauResponse:
        base_payload, active_event_profile, active_event_profile_id, active_event_profile_hash = (
            self._bind_backtest_event_judgment_profile(payload.base_payload)
        )
        if base_payload != payload.base_payload:
            payload = payload.model_copy(update={"base_payload": base_payload}, deep=True)
        with self._backtest_runtime_event_judgment_binding(
            profile=active_event_profile,
            profile_id=active_event_profile_id,
            profile_hash=active_event_profile_hash,
        ):
            return self._run_backtest_plateau_bound(
                payload,
                task_id=task_id,
                progress_callback=progress_callback,
                control_callback=control_callback,
            )

    def _run_backtest_plateau_bound(
        self,
        payload: BacktestPlateauRunRequest,
        *,
        task_id: str | None = None,
        progress_callback: Callable[[int, int, str], None] | None = None,
        control_callback: Callable[[], None] | None = None,
    ) -> BacktestPlateauResponse:
        base = payload.base_payload.model_copy(deep=True)
        window_axis = self._normalize_plateau_axis_int(
            payload.window_days_list,
            base=int(base.window_days),
            lower=20,
            upper=240,
        )
        min_score_axis = self._normalize_plateau_axis_float(
            payload.min_score_list,
            base=float(base.min_score),
            lower=0.0,
            upper=100.0,
            precision=4,
        )
        stop_loss_axis = self._normalize_plateau_axis_float(
            payload.stop_loss_list,
            base=float(base.stop_loss),
            lower=0.0,
            upper=0.5,
            precision=6,
        )
        take_profit_axis = self._normalize_plateau_axis_float(
            payload.take_profit_list,
            base=float(base.take_profit),
            lower=0.0,
            upper=1.5,
            precision=6,
        )
        trailing_stop_axis = self._normalize_plateau_axis_float(
            payload.trailing_stop_pct_list,
            base=float(base.trailing_stop_pct),
            lower=0.0,
            upper=0.5,
            precision=6,
        )
        intraday_reduce_axis = self._normalize_plateau_axis_float(
            payload.intraday_trailing_reduce_ratio_list,
            base=float(base.intraday_trailing_reduce_ratio),
            lower=0.1,
            upper=1.0,
            precision=6,
        )
        max_positions_axis = self._normalize_plateau_axis_int(
            payload.max_positions_list,
            base=int(base.max_positions),
            lower=1,
            upper=100,
        )
        position_pct_axis = self._normalize_plateau_axis_float(
            payload.position_pct_list,
            base=float(base.position_pct),
            lower=0.0001,
            upper=1.0,
            precision=6,
        )
        max_symbols_axis = self._normalize_plateau_axis_int(
            payload.max_symbols_list,
            base=int(base.max_symbols),
            lower=20,
            upper=2000,
        )
        priority_topk_axis = self._normalize_plateau_axis_int(
            payload.priority_topk_per_day_list,
            base=int(base.priority_topk_per_day),
            lower=0,
            upper=500,
        )

        axis_lengths = [
            len(window_axis),
            len(min_score_axis),
            len(stop_loss_axis),
            len(take_profit_axis),
            len(trailing_stop_axis),
            len(intraday_reduce_axis),
            len(max_positions_axis),
            len(position_pct_axis),
            len(max_symbols_axis),
            len(priority_topk_axis),
        ]
        grid_total_combinations = 1
        for size in axis_lengths:
            grid_total_combinations *= max(1, int(size))

        sampling_mode = payload.sampling_mode
        sample_points = self._resolve_plateau_sample_points(payload)
        if sampling_mode == "grid":
            if grid_total_combinations > 2000:
                raise ValueError(f"完整网格共 {grid_total_combinations} 点，超过单次2000点；请缩小参数范围或使用区间采样")
            # A grid is the complete Cartesian product. Never evaluate a lexicographic prefix.
            sample_points = grid_total_combinations

        params_to_evaluate: list[BacktestPlateauParams] = []
        total_combinations = int(grid_total_combinations)
        if sampling_mode == "lhs":
            total_combinations = int(sample_points)
            params_to_evaluate = self._build_plateau_lhs_params(
                point_count=sample_points,
                random_seed=payload.random_seed,
                window_axis=window_axis,
                min_score_axis=min_score_axis,
                stop_loss_axis=stop_loss_axis,
                take_profit_axis=take_profit_axis,
                trailing_stop_axis=trailing_stop_axis,
                intraday_reduce_axis=intraday_reduce_axis,
                max_positions_axis=max_positions_axis,
                position_pct_axis=position_pct_axis,
                max_symbols_axis=max_symbols_axis,
                priority_topk_axis=priority_topk_axis,
            )
        else:
            for combo in product(
                window_axis,
                min_score_axis,
                stop_loss_axis,
                take_profit_axis,
                trailing_stop_axis,
                intraday_reduce_axis,
                max_positions_axis,
                position_pct_axis,
                max_symbols_axis,
                priority_topk_axis,
            ):
                if len(params_to_evaluate) >= sample_points:
                    break
                (
                    window_days,
                    min_score,
                    stop_loss,
                    take_profit,
                    trailing_stop_pct,
                    intraday_trailing_reduce_ratio,
                    max_positions,
                    position_pct,
                    max_symbols,
                    priority_topk_per_day,
                ) = combo
                params_to_evaluate.append(
                    BacktestPlateauParams(
                        window_days=int(window_days),
                        min_score=float(min_score),
                        stop_loss=float(stop_loss),
                        take_profit=float(take_profit),
                        trailing_stop_pct=float(trailing_stop_pct),
                        intraday_trailing_reduce_ratio=float(intraday_trailing_reduce_ratio),
                        max_positions=int(max_positions),
                        position_pct=float(position_pct),
                        max_symbols=int(max_symbols),
                        priority_topk_per_day=int(priority_topk_per_day),
                    )
                )

        total_to_evaluate = max(1, len(params_to_evaluate))
        worker_count = max(1, min(self._backtest_plateau_eval_workers(), total_to_evaluate))
        evaluation_lock = RLock()
        points_slots: list[BacktestPlateauPoint | None] = [None] * len(params_to_evaluate)
        failure_count = 0
        evaluated = 0

        def _build_failed_point(params: BacktestPlateauParams, exc: Exception) -> BacktestPlateauPoint:
            return BacktestPlateauPoint(
                params=params,
                stats=ReviewStats(
                    win_rate=0.0,
                    total_return=0.0,
                    max_drawdown=0.0,
                    avg_pnl_ratio=0.0,
                    trade_count=0,
                    win_count=0,
                    loss_count=0,
                    profit_factor=0.0,
                ),
                candidate_count=0,
                skipped_count=0,
                fill_rate=0.0,
                max_concurrent_positions=0,
                score=-9999.0,
                cache_hit=False,
                error=str(exc),
            )

        def _evaluate_single_point(
            index: int,
            params: BacktestPlateauParams,
            universe: tuple[list[str], dict[str, set[str]] | None, list[str]] | None = None,
            cached_candidates: list[CandidateTrade] | None = None,
        ) -> tuple[int, BacktestPlateauPoint, bool]:
            if control_callback is not None:
                control_callback()
            detail_key = (
                self._build_backtest_plateau_detail_key(params)
                if task_id
                else None
            )
            run_payload = self._build_backtest_plateau_run_payload(base, params)
            try:
                if cached_candidates is not None:
                    result = _plateau_engine.replay_portfolio(
                        candidates=cached_candidates,
                        payload=run_payload,
                    )
                    if task_id and detail_key:
                        self._persist_backtest_plateau_point_detail(
                            task_id=task_id,
                            detail_key=detail_key,
                            params=params,
                            run_request=run_payload,
                            run_result=result,
                        )
                    return (
                        index,
                        BacktestPlateauPoint(
                            params=params,
                            stats=result.stats,
                            candidate_count=int(result.candidate_count),
                            skipped_count=int(result.skipped_count),
                            fill_rate=float(result.fill_rate),
                            max_concurrent_positions=int(result.max_concurrent_positions),
                            score=self._backtest_plateau_score(result),
                            cache_hit=True,
                            detail_key=detail_key,
                            error=None,
                        ),
                        False,
                    )
                result = self.run_backtest(
                    run_payload,
                    control_callback=control_callback,
                    prebuilt_universe=universe,
                    feature_cache=(cached_row, cached_snapshot),
                )
                if task_id and detail_key:
                    self._persist_backtest_plateau_point_detail(
                        task_id=task_id,
                        detail_key=detail_key,
                        params=params,
                        run_request=run_payload,
                        run_result=result,
                    )
                cache_hit = any("回测结果缓存命中" in str(note) for note in result.notes)
                return (
                    index,
                    BacktestPlateauPoint(
                        params=params,
                        stats=result.stats,
                        candidate_count=int(result.candidate_count),
                        skipped_count=int(result.skipped_count),
                        fill_rate=float(result.fill_rate),
                        max_concurrent_positions=int(result.max_concurrent_positions),
                        score=self._backtest_plateau_score(result),
                        cache_hit=cache_hit,
                        detail_key=detail_key,
                        error=None,
                    ),
                    False,
                )
            except BacktestTaskCancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                return index, _build_failed_point(params, exc), True

        def _record_eval_result(index: int, point: BacktestPlateauPoint, failed: bool) -> None:
            nonlocal evaluated, failure_count
            with evaluation_lock:
                points_slots[index] = point
                evaluated += 1
                if failed:
                    failure_count += 1
                evaluated_now = int(evaluated)
            if progress_callback is not None:
                progress_callback(
                    evaluated_now,
                    int(total_to_evaluate),
                    f"收益平原评估中：{evaluated_now}/{total_to_evaluate}",
                )

        cancelled_exc: BacktestTaskCancelledError | None = None

        # --- Pre-build universes by max_symbols to avoid redundant pool construction ---
        # 优化：只用最大 max_symbols 构建一次完整 universe，较小值从中截断。
        # TDX 数据加载和 screener 筛选不依赖 max_symbols，max_symbols 只影响每日截断数量。
        board_filters_for_prebuild = [
            item for item in base.board_filters if item in {"main", "gem", "star", "beijing", "st"}
        ]
        can_prebuild = base.pool_roll_mode in {"daily", "weekly"}
        universe_by_max_symbols: dict[int, tuple[list[str], dict[str, set[str]] | None, list[str]]] = {}
        if can_prebuild:
            unique_max_symbols = sorted({int(p.max_symbols) for p in params_to_evaluate})
            max_ms = unique_max_symbols[-1] if unique_max_symbols else int(base.max_symbols)
            if control_callback is not None:
                control_callback()
            if progress_callback is not None:
                progress_callback(
                    0,
                    int(total_to_evaluate),
                    f"候选池预构建（max_symbols={max_ms}，共 {len(unique_max_symbols)} 个值）",
                )
            try:
                prebuild_payload = base.model_copy(update={"max_symbols": max_ms}, deep=True)
                full_universe = self._build_backtest_universe_for_plateau(
                    prebuild_payload,
                    board_filters_for_prebuild,
                    control_callback=control_callback,
                )
                full_symbols, full_allowed, full_notes = full_universe
                universe_by_max_symbols[max_ms] = full_universe
                # 对较小的 max_symbols 值，从完整结果中截断 allowed_symbols_by_date
                for ms_val in unique_max_symbols:
                    if ms_val == max_ms:
                        continue
                    if full_allowed is not None:
                        truncated_allowed: dict[str, set[str]] = {}
                        truncated_symbols_union: set[str] = set()
                        for date_key, date_symbols in full_allowed.items():
                            truncated_day = set(list(date_symbols)[:ms_val])
                            truncated_allowed[date_key] = truncated_day
                            truncated_symbols_union.update(truncated_day)
                        truncated_notes = list(full_notes) + [
                            f"候选池从 max_symbols={max_ms} 截断到 {ms_val}。"
                        ]
                        universe_by_max_symbols[ms_val] = (
                            sorted(truncated_symbols_union),
                            truncated_allowed,
                            truncated_notes,
                        )
                    else:
                        # full_allowed is None means no per-date restriction
                        truncated_syms = full_symbols[:ms_val]
                        truncated_notes = list(full_notes) + [
                            f"候选池从 max_symbols={max_ms} 截断到 {ms_val}。"
                        ]
                        universe_by_max_symbols[ms_val] = (truncated_syms, None, truncated_notes)
            except BacktestTaskCancelledError:
                raise
            except Exception:
                pass  # will fall back to run_backtest building its own universe
            if progress_callback is not None:
                progress_callback(
                    0,
                    int(total_to_evaluate),
                    f"候选池预构建完成（{len(universe_by_max_symbols)} 个 max_symbols 值）",
                )

        # Sort params by (window_days, max_symbols) to maximize Wyckoff snapshot cache hits.
        # The first run for each window_days warms the cache; subsequent runs hit it.
        indexed_params = list(enumerate(params_to_evaluate))
        indexed_params.sort(key=lambda item: (item[1].window_days, item[1].max_symbols))

        from .core.plateau_features import feature_readers

        cached_row, cached_snapshot = feature_readers(
            self._build_row_from_candles,
            self._calc_wyckoff_snapshot,
            lambda: str((self._get_backtest_runtime_event_judgment_binding() or {}).get("profile_hash", "")),
        )
        _plateau_engine = BacktestEngine(
            get_candles=self._ensure_candles,
            build_row=cached_row,
            calc_snapshot=cached_snapshot,
            resolve_symbol_name=self._resolve_symbol_name,
            strategy_signal_filter=lambda strategy_id, row, snapshot, params: self._strategy_registry.generate_signals(
                strategy_id=strategy_id,
                row=row,
                snapshot=snapshot,
                params=params,
            ),
            strategy_signal_context_builder=lambda strategy_id, snapshot, params: self._build_strategy_signal_context(
                strategy_id,
                snapshot,
                params,
            ),
        )

        def _candidate_cache_key(p: BacktestPlateauParams) -> tuple:
            return (
                str(base.event_judgment_profile_hash or "").strip(),
                int(p.window_days),
                float(p.min_score),
                float(p.stop_loss),
                float(p.take_profit),
                float(p.trailing_stop_pct),
                int(p.max_symbols),
            )

        from collections import defaultdict as _ddict

        groups_by_ckey: dict[tuple, list[tuple[int, BacktestPlateauParams]]] = _ddict(list)
        for orig_idx, params in indexed_params:
            groups_by_ckey[_candidate_cache_key(params)].append((orig_idx, params))

        primary_plan: list[tuple[int, BacktestPlateauParams, tuple]] = []
        replay_plan: list[tuple[int, BacktestPlateauParams, tuple]] = []
        for ckey, group in groups_by_ckey.items():
            primary_plan.append((group[0][0], group[0][1], ckey))
            for orig_idx, params in group[1:]:
                replay_plan.append((orig_idx, params, ckey))

        reuse_groups = sum(1 for group in groups_by_ckey.values() if len(group) > 1)
        reuse_points = len(replay_plan)
        if reuse_points > 0 and progress_callback is not None:
            progress_callback(
                0,
                int(total_to_evaluate),
                f"候选交易缓存优化：{reuse_groups} 组共享候选，{reuse_points} 个点可快速重放",
            )

        candidate_cache: dict[tuple, list[CandidateTrade]] = {}
        candidate_cache_failed: set[tuple] = set()
        candidate_cache_building: dict[tuple, Event] = {}
        candidate_cache_lock = RLock()

        def _build_candidates_for_key(
            ckey: tuple,
            params: BacktestPlateauParams,
            universe: tuple[list[str], dict[str, set[str]] | None, list[str]] | None,
        ) -> list[CandidateTrade] | None:
            run_payload = base.model_copy(
                update={
                    "window_days": params.window_days,
                    "min_score": params.min_score,
                    "stop_loss": params.stop_loss,
                    "take_profit": params.take_profit,
                    "trailing_stop_pct": params.trailing_stop_pct,
                    "max_positions": params.max_positions,
                    "position_pct": params.position_pct,
                    "max_symbols": params.max_symbols,
                    "priority_topk_per_day": params.priority_topk_per_day,
                    "enable_advanced_analysis": False,
                },
                deep=True,
            )
            _ = ckey
            bound_profile = (
                dict(run_payload.event_judgment_profile_snapshot)
                if isinstance(run_payload.event_judgment_profile_snapshot, dict)
                else {}
            )
            bound_profile_id = (
                str(run_payload.event_judgment_profile_id or "").strip()
                or str(base.event_judgment_profile_id or "").strip()
                or self._default_event_judgment_profile_id()
            )
            bound_profile_hash = (
                str(run_payload.event_judgment_profile_hash or "").strip()
                or str(base.event_judgment_profile_hash or "").strip()
                or self._event_judgment_profile_hash(bound_profile)
            )
            try:
                symbols_for_run = universe[0] if universe else []
                allowed_for_run = universe[1] if universe else None
                if not symbols_for_run:
                    return None
                with self._backtest_runtime_event_judgment_binding(
                    profile=bound_profile,
                    profile_id=bound_profile_id,
                    profile_hash=bound_profile_hash,
                ):
                    return _plateau_engine.run_candidates_only(
                        payload=run_payload,
                        symbols=symbols_for_run,
                        allowed_symbols_by_date=allowed_for_run,
                        apply_priority_topk=False,
                        control_callback=control_callback,
                    )
            except BacktestTaskCancelledError:
                raise
            except Exception:
                return None

        def _get_or_build_candidates(
            params: BacktestPlateauParams,
            universe: tuple[list[str], dict[str, set[str]] | None, list[str]] | None,
        ) -> list[CandidateTrade] | None:
            ckey = _candidate_cache_key(params)
            while True:
                owner = False
                wait_event: Event | None = None
                with candidate_cache_lock:
                    cached = candidate_cache.get(ckey)
                    if cached is not None:
                        return cached
                    if ckey in candidate_cache_failed:
                        return None
                    wait_event = candidate_cache_building.get(ckey)
                    if wait_event is None:
                        wait_event = Event()
                        candidate_cache_building[ckey] = wait_event
                        owner = True

                if owner:
                    built: list[CandidateTrade] | None = None
                    cancelled = False
                    try:
                        built = _build_candidates_for_key(ckey, params, universe)
                    except BacktestTaskCancelledError:
                        cancelled = True
                        raise
                    finally:
                        with candidate_cache_lock:
                            if built is not None:
                                candidate_cache[ckey] = built
                                candidate_cache_failed.discard(ckey)
                            elif not cancelled:
                                candidate_cache_failed.add(ckey)
                            event = candidate_cache_building.pop(ckey, None)
                            if event is not None:
                                event.set()
                    return built

                if wait_event is None:
                    continue
                while True:
                    if control_callback is not None:
                        control_callback()
                    if wait_event.wait(timeout=0.1):
                        break

        def _evaluate_replay_point(
            orig_idx: int,
            params: BacktestPlateauParams,
            universe: tuple[list[str], dict[str, set[str]] | None, list[str]] | None,
        ) -> tuple[int, BacktestPlateauPoint, bool]:
            cached = _get_or_build_candidates(params, universe)
            if cached is not None:
                return _evaluate_single_point(orig_idx, params, universe, cached_candidates=cached)
            return _evaluate_single_point(orig_idx, params, universe)

        def _run_plan_parallel(
            plan: list[tuple[int, BacktestPlateauParams, tuple]],
            evaluator: Callable[
                [int, BacktestPlateauParams, tuple[list[str], dict[str, set[str]] | None, list[str]] | None],
                tuple[int, BacktestPlateauPoint, bool],
            ],
        ) -> None:
            nonlocal cancelled_exc
            if not plan:
                return
            executor = ThreadPoolExecutor(max_workers=worker_count)
            cancelled_during_run = False
            try:
                future_to_meta: dict[Any, tuple[int, BacktestPlateauParams, tuple]] = {}
                pending = iter(plan)

                def _submit_next() -> None:
                    if cancelled_exc is not None:
                        return
                    try:
                        orig_idx, params, ckey = next(pending)
                    except StopIteration:
                        return
                    universe = universe_by_max_symbols.get(int(params.max_symbols)) if can_prebuild else None
                    future = executor.submit(evaluator, orig_idx, params, universe)
                    future_to_meta[future] = (orig_idx, params, ckey)

                for _ in range(min(worker_count, len(plan))):
                    _submit_next()

                while future_to_meta:
                    done_future = next(as_completed(list(future_to_meta.keys())))
                    fallback_idx, fallback_params, _ = future_to_meta.pop(done_future)
                    try:
                        idx_out, point, failed = done_future.result()
                    except BacktestTaskCancelledError as exc:
                        cancelled_exc = exc
                        cancelled_during_run = True
                        for pending_future in list(future_to_meta.keys()):
                            pending_future.cancel()
                        future_to_meta.clear()
                        break
                    except Exception as exc:  # noqa: BLE001
                        idx_out = int(fallback_idx)
                        point = _build_failed_point(fallback_params, exc)
                        failed = True
                    _record_eval_result(idx_out, point, failed)
                    _submit_next()
            finally:
                executor.shutdown(wait=not cancelled_during_run, cancel_futures=cancelled_during_run)

        if worker_count <= 1 or len(params_to_evaluate) <= 1:
            for orig_idx, params, _ in primary_plan:
                universe = universe_by_max_symbols.get(int(params.max_symbols)) if can_prebuild else None
                idx_out, point, failed = _evaluate_single_point(orig_idx, params, universe)
                _record_eval_result(idx_out, point, failed)
            for orig_idx, params, _ in replay_plan:
                universe = universe_by_max_symbols.get(int(params.max_symbols)) if can_prebuild else None
                idx_out, point, failed = _evaluate_replay_point(orig_idx, params, universe)
                _record_eval_result(idx_out, point, failed)
        else:
            _run_plan_parallel(
                primary_plan,
                lambda idx, params, universe: _evaluate_single_point(idx, params, universe),
            )
            if cancelled_exc is None:
                _run_plan_parallel(replay_plan, _evaluate_replay_point)

        if cancelled_exc is not None:
            raise cancelled_exc

        points = [point for point in points_slots if point is not None]
        valid_points, neighbor_meta = self._apply_plateau_robustness_scores(
            points,
            base_payload=base,
        )
        regions = self._build_backtest_plateau_regions(
            valid_points,
            neighbor_meta,
            base_payload=base,
            control_callback=control_callback,
        )
        points.sort(
            key=lambda row: (
                row.error is None,
                row.plateau_score,
                row.local_score,
                row.point_score,
                row.score,
                row.stats.total_return,
                row.stats.win_rate,
            ),
            reverse=True,
        )
        peak_point = None
        if valid_points:
            peak_point = max(
                valid_points,
                key=lambda row: (
                    row.score,
                    row.stats.total_return,
                    row.stats.win_rate,
                ),
            )
        recommended_point = self._select_backtest_plateau_recommendation(regions)
        best_point = recommended_point
        correlations = self._build_backtest_plateau_correlations(points)
        notes: list[str] = []
        if sampling_mode == "grid" and grid_total_combinations > sample_points:
            notes.append(
                f"参数组合总数 {grid_total_combinations} 超过上限，已截断评估前 {sample_points} 组。"
            )
        if sampling_mode == "lhs":
            notes.append(f"参数采样模式: lhs，目标采样 {sample_points} 组。")
            if payload.random_seed is not None:
                notes.append(f"LHS 随机种子: {payload.random_seed}。")
            if len(params_to_evaluate) < sample_points:
                notes.append(
                    f"LHS 去重后仅生成 {len(params_to_evaluate)} 组有效参数（目标 {sample_points} 组）。"
                )
            notes.append(f"参考网格组合规模（按列表离散值估算）: {grid_total_combinations}。")
        notes.append(f"收益平原并行评估线程数: {worker_count}。")
        if reuse_points > 0:
            notes.append(f"候选重放复用: {reuse_groups} 组共享，复用点位 {reuse_points}。")
        if can_prebuild and universe_by_max_symbols:
            notes.append(
                f"候选池预构建优化: {len(universe_by_max_symbols)} 个不同 max_symbols 值共享候选池，"
                f"参数按 (window_days, max_symbols) 排序以最大化 snapshot 缓存命中。"
            )
        if failure_count > 0:
            notes.append(f"有 {failure_count} 组参数评估失败，详情见 points.error。")
        notes.append("收益平原 V1 改为优先寻找稳定区域：先过硬门槛，再计算单点质量分、局部分与收益平原分。")
        notes.append("硬门槛包含：收益>0、ProfitFactor>=1.05、回撤<=35%、fill_rate>=10%、年化交易数>=12、单笔收益能覆盖成本。")
        notes.append("自动区域识别仅纳入通过硬门槛且局部分>=55 的参数组；推荐参数默认取区域中心，而不是历史峰值。")
        if recommended_point is None:
            notes.append("未发现符合条件的稳定区域，不推荐任何参数；排名和历史峰值仅用于查看计算结果。")
        if regions:
            notes.append(
                f"自动识别收益平原区域 {len(regions)} 个，Top1 区域包含 {regions[0].point_count} 组参数，"
                f"区域分 {regions[0].region_score:.1f}。"
            )
            if regions[0].oos_pass_rate is not None:
                notes.append(
                    f"Top1 区域中心样本外通过率 {regions[0].oos_pass_rate:.1f}%（walk-forward {regions[0].walk_forward.fold_count if regions[0].walk_forward else 0} 折）。"
                )
            elif regions[0].walk_forward is not None and regions[0].walk_forward.notes:
                notes.append(f"Top1 区域中心样本外验证未形成有效折叠：{regions[0].walk_forward.notes[0]}")
        if peak_point is not None and recommended_point is not None and peak_point is not recommended_point:
            notes.append(
                "best_point 已改为推荐区域中心；peak_point 保留原始评分峰值，便于对比鲁棒性与历史最优的差异。"
            )
        score_corr_note = self._build_plateau_correlation_summary_note(
            correlations,
            metric_label="原始评分",
            corr_field="score_corr",
        )
        if score_corr_note:
            notes.append(score_corr_note)
        return_corr_note = self._build_plateau_correlation_summary_note(
            correlations,
            metric_label="收益",
            corr_field="total_return_corr",
        )
        if return_corr_note:
            notes.append(return_corr_note)
        win_rate_corr_note = self._build_plateau_correlation_summary_note(
            correlations,
            metric_label="胜率",
            corr_field="win_rate_corr",
        )
        if win_rate_corr_note:
            notes.append(win_rate_corr_note)
        notes.append(
            f"收益平原评估完成：总组合 {total_combinations}，实际评估 {evaluated}。"
        )
        return BacktestPlateauResponse(
            base_payload=base,
            strategy_version=self._strategy_registry.get(base.strategy_id).version,
            total_combinations=int(total_combinations),
            evaluated_combinations=int(evaluated),
            points=points,
            best_point=best_point,
            recommended_point=recommended_point,
            peak_point=peak_point,
            regions=regions,
            correlations=correlations,
            generated_at=self._now_datetime(),
            notes=notes,
        )

    @staticmethod
    def _quantile(values: list[float], ratio: float) -> float:
        if not values:
            return 0.0
        clipped = max(0.0, min(1.0, float(ratio)))
        sorted_values = sorted(float(item) for item in values)
        if len(sorted_values) == 1:
            return float(sorted_values[0])
        pos = clipped * float(len(sorted_values) - 1)
        lo = int(math.floor(pos))
        hi = int(math.ceil(pos))
        if lo == hi:
            return float(sorted_values[lo])
        weight = pos - float(lo)
        return float(sorted_values[lo] * (1.0 - weight) + sorted_values[hi] * weight)

    @staticmethod
    def _plateau_params_from_payload(payload: BacktestRunRequest) -> BacktestPlateauParams:
        return BacktestPlateauParams(
            window_days=int(payload.window_days),
            min_score=float(payload.min_score),
            stop_loss=float(payload.stop_loss),
            take_profit=float(payload.take_profit),
            trailing_stop_pct=float(payload.trailing_stop_pct),
            intraday_trailing_reduce_ratio=float(payload.intraday_trailing_reduce_ratio),
            max_positions=int(payload.max_positions),
            position_pct=float(payload.position_pct),
            max_symbols=int(payload.max_symbols),
            priority_topk_per_day=int(payload.priority_topk_per_day),
        )

    def _compute_backtest_risk_metrics(
        self,
        payload: BacktestRunRequest,
        result: BacktestResponse,
    ) -> BacktestRiskMetrics:
        _ = payload
        trades = sorted(
            list(result.trades),
            key=lambda row: (str(row.exit_date), str(row.entry_date), str(row.symbol)),
        )
        pnl_ratios = [float(row.pnl_ratio) for row in trades if math.isfinite(float(row.pnl_ratio))]
        win_ratios = [value for value in pnl_ratios if value > 0.0]
        loss_ratios = [value for value in pnl_ratios if value < 0.0]
        avg_win_ratio = self._safe_mean(win_ratios)
        avg_loss_ratio = self._safe_mean(loss_ratios)
        win_rate = float(result.stats.win_rate)
        expectancy = win_rate * avg_win_ratio + (1.0 - win_rate) * avg_loss_ratio

        max_consecutive_losses = 0
        current_losses = 0
        for value in pnl_ratios:
            if value < 0.0:
                current_losses += 1
                max_consecutive_losses = max(max_consecutive_losses, current_losses)
            else:
                current_losses = 0

        daily_returns: list[float] = []
        equity_curve = list(result.equity_curve)
        for idx in range(1, len(equity_curve)):
            prev_equity = float(equity_curve[idx - 1].equity)
            curr_equity = float(equity_curve[idx].equity)
            if not (math.isfinite(prev_equity) and math.isfinite(curr_equity)):
                continue
            if prev_equity <= 0:
                continue
            daily_returns.append(curr_equity / prev_equity - 1.0)

        sharpe = 0.0
        sortino = 0.0
        if daily_returns:
            mean_daily = float(np.mean(daily_returns))
            std_daily = float(np.std(daily_returns))
            if std_daily > 1e-12:
                sharpe = mean_daily / std_daily * math.sqrt(252.0)
            downside_returns = [item for item in daily_returns if item < 0.0]
            downside_std = float(np.std(downside_returns)) if downside_returns else 0.0
            if downside_std > 1e-12:
                sortino = mean_daily / downside_std * math.sqrt(252.0)

        max_drawdown = float(result.stats.max_drawdown)
        calmar = 0.0
        if max_drawdown > 1e-9:
            calmar = float(result.stats.total_return) / max_drawdown

        recovery_days = 0
        drawdowns = list(result.drawdown_curve)
        if drawdowns:
            trough_idx = min(range(len(drawdowns)), key=lambda idx: float(drawdowns[idx].drawdown))
            trough_value = float(drawdowns[trough_idx].drawdown)
            if trough_value < -1e-9:
                recovery_idx = next(
                    (idx for idx in range(trough_idx + 1, len(drawdowns)) if float(drawdowns[idx].drawdown) >= -1e-6),
                    None,
                )
                start_dt = self._parse_date(drawdowns[trough_idx].date)
                end_dt = self._parse_date(drawdowns[recovery_idx].date) if recovery_idx is not None else self._parse_date(drawdowns[-1].date)
                if start_dt is not None and end_dt is not None:
                    recovery_days = max(0, int((end_dt - start_dt).days))
                elif recovery_idx is not None:
                    recovery_days = max(0, recovery_idx - trough_idx)
                else:
                    recovery_days = max(0, len(drawdowns) - 1 - trough_idx)

        return BacktestRiskMetrics(
            sharpe=round(float(sharpe), 6),
            sortino=round(float(sortino), 6),
            calmar=round(float(calmar), 6),
            expectancy=round(float(expectancy), 6),
            avg_win_pnl_ratio=round(float(avg_win_ratio), 6),
            avg_loss_pnl_ratio=round(float(avg_loss_ratio), 6),
            max_consecutive_losses=int(max_consecutive_losses),
            recovery_days=int(recovery_days),
        )

    def _compute_backtest_regime_breakdown(
        self,
        payload: BacktestRunRequest,
        result: BacktestResponse,
    ) -> list[BacktestRegimeBucket]:
        equity_curve = list(result.equity_curve)
        drawdown_curve = list(result.drawdown_curve)
        if not equity_curve:
            return [
                BacktestRegimeBucket(regime="bull", label="牛市代理"),
                BacktestRegimeBucket(regime="range", label="震荡代理"),
                BacktestRegimeBucket(regime="bear", label="熊市代理"),
            ]

        date_axis = [str(item.date) for item in equity_curve]
        equity_values = [float(item.equity) for item in equity_curve]
        drawdown_by_date = {str(item.date): float(item.drawdown) for item in drawdown_curve}
        regime_by_date: dict[str, Literal["bull", "range", "bear"]] = {}
        regime_window = min(20, max(5, len(date_axis) // 3))
        for idx, day in enumerate(date_axis):
            if idx < regime_window:
                regime_by_date[day] = "range"
                continue
            prev = float(equity_values[idx - regime_window])
            curr = float(equity_values[idx])
            rolling_return = (curr / prev - 1.0) if prev > 0 else 0.0
            drawdown = float(drawdown_by_date.get(day, 0.0))
            if rolling_return >= 0.03 and drawdown >= -0.08:
                regime_by_date[day] = "bull"
            elif rolling_return <= -0.03 or drawdown <= -0.15:
                regime_by_date[day] = "bear"
            else:
                regime_by_date[day] = "range"

        bucket_data: dict[str, dict[str, float]] = {
            "bull": {"trade_count": 0.0, "win_count": 0.0, "pnl_sum": 0.0, "pnl_ratio_sum": 0.0},
            "range": {"trade_count": 0.0, "win_count": 0.0, "pnl_sum": 0.0, "pnl_ratio_sum": 0.0},
            "bear": {"trade_count": 0.0, "win_count": 0.0, "pnl_sum": 0.0, "pnl_ratio_sum": 0.0},
        }
        regime_drawdowns: dict[str, list[float]] = {"bull": [], "range": [], "bear": []}
        for day, regime in regime_by_date.items():
            regime_drawdowns[regime].append(abs(min(0.0, float(drawdown_by_date.get(day, 0.0)))))

        for trade in result.trades:
            entry_date = str(trade.entry_date)
            idx = bisect_right(date_axis, entry_date) - 1
            if idx < 0:
                regime = "range"
            else:
                regime = regime_by_date.get(date_axis[idx], "range")
            target = bucket_data[regime]
            target["trade_count"] += 1.0
            if float(trade.pnl_ratio) > 0.0:
                target["win_count"] += 1.0
            target["pnl_sum"] += float(trade.pnl_amount)
            target["pnl_ratio_sum"] += float(trade.pnl_ratio)

        out: list[BacktestRegimeBucket] = []
        for regime, label in (("bull", "牛市代理"), ("range", "震荡代理"), ("bear", "熊市代理")):
            data = bucket_data[regime]
            trade_count = int(data["trade_count"])
            win_rate = (data["win_count"] / data["trade_count"]) if data["trade_count"] > 0 else 0.0
            avg_ratio = (data["pnl_ratio_sum"] / data["trade_count"]) if data["trade_count"] > 0 else 0.0
            out.append(
                BacktestRegimeBucket(
                    regime=regime,  # type: ignore[arg-type]
                    label=label,
                    trade_count=trade_count,
                    win_rate=round(float(win_rate), 6),
                    total_return=round(float(data["pnl_sum"] / payload.initial_capital), 6) if payload.initial_capital > 0 else 0.0,
                    avg_pnl_ratio=round(float(avg_ratio), 6),
                    max_drawdown=round(float(max(regime_drawdowns[regime]) if regime_drawdowns[regime] else 0.0), 6),
                )
            )
        return out

    def _compute_backtest_monte_carlo(
        self,
        result: BacktestResponse,
        *,
        simulations: int = 400,
        seed: int = 20260223,
    ) -> BacktestMonteCarloSummary:
        trade_returns = [
            float(row.pnl_ratio)
            for row in result.trades
            if math.isfinite(float(row.pnl_ratio))
        ]
        if len(trade_returns) < 2:
            return BacktestMonteCarloSummary(simulations=0, seed=int(seed))

        rng = random.Random(int(seed))
        total_returns: list[float] = []
        drawdowns: list[float] = []
        sim_count = max(1, int(simulations))
        for _ in range(sim_count):
            shuffled = list(trade_returns)
            rng.shuffle(shuffled)
            equity = 1.0
            peak = 1.0
            max_drawdown_raw = 0.0
            for trade_ret in shuffled:
                stress = rng.uniform(-0.003, 0.001)
                effective_ret = max(-0.95, float(trade_ret) + stress)
                equity *= (1.0 + effective_ret)
                peak = max(peak, equity)
                drawdown = equity / peak - 1.0 if peak > 0 else 0.0
                max_drawdown_raw = min(max_drawdown_raw, drawdown)
            total_returns.append(equity - 1.0)
            drawdowns.append(abs(max_drawdown_raw))

        ruin_probability = sum(1 for item in total_returns if item <= -0.2) / float(sim_count)
        return BacktestMonteCarloSummary(
            simulations=sim_count,
            seed=int(seed),
            total_return_p5=round(self._quantile(total_returns, 0.05), 6),
            total_return_p50=round(self._quantile(total_returns, 0.50), 6),
            total_return_p95=round(self._quantile(total_returns, 0.95), 6),
            max_drawdown_p5=round(self._quantile(drawdowns, 0.05), 6),
            max_drawdown_p50=round(self._quantile(drawdowns, 0.50), 6),
            max_drawdown_p95=round(self._quantile(drawdowns, 0.95), 6),
            ruin_probability=round(float(ruin_probability), 6),
        )

    @staticmethod
    def _clone_payload_with_updates(payload: BacktestRunRequest, *, updates: dict[str, Any]) -> BacktestRunRequest:
        return payload.model_copy(update=updates, deep=True)

    def _build_backtest_neighbor_payloads(self, payload: BacktestRunRequest) -> list[BacktestRunRequest]:
        def _clamp(value: float, lower: float, upper: float, precision: int = 6) -> float:
            return round(max(lower, min(upper, float(value))), int(precision))

        def _clamp_int(value: int, lower: int, upper: int) -> int:
            return max(int(lower), min(int(upper), int(value)))

        updates_list = [
            {"min_score": _clamp(payload.min_score - 5.0, 0.0, 100.0, 4)},
            {"min_score": _clamp(payload.min_score + 5.0, 0.0, 100.0, 4)},
            {"window_days": _clamp_int(payload.window_days - 20, 20, 240)},
            {"window_days": _clamp_int(payload.window_days + 20, 20, 240)},
            {
                "stop_loss": _clamp(payload.stop_loss + 0.01, 0.0, 0.5, 6),
                "take_profit": _clamp(payload.take_profit + 0.03, 0.0, 1.5, 6),
            },
            {
                "stop_loss": _clamp(payload.stop_loss - 0.01, 0.0, 0.5, 6),
                "take_profit": _clamp(payload.take_profit - 0.03, 0.0, 1.5, 6),
            },
        ]
        seen: set[tuple[float, ...]] = set()
        out: list[BacktestRunRequest] = []
        base_key = (
            float(payload.window_days),
            float(payload.min_score),
            float(payload.stop_loss),
            float(payload.take_profit),
            float(payload.max_positions),
            float(payload.position_pct),
            float(payload.max_symbols),
            float(payload.priority_topk_per_day),
        )
        for updates in updates_list:
            merged_updates = {"enable_advanced_analysis": False, **updates}
            candidate = self._clone_payload_with_updates(payload, updates=merged_updates)
            key = (
                float(candidate.window_days),
                float(candidate.min_score),
                float(candidate.stop_loss),
                float(candidate.take_profit),
                float(candidate.max_positions),
                float(candidate.position_pct),
                float(candidate.max_symbols),
                float(candidate.priority_topk_per_day),
            )
            if key in seen or key == base_key:
                continue
            seen.add(key)
            out.append(candidate)
            if len(out) >= 6:
                break
        return out

    def _run_backtest_neighborhood_probe(
        self,
        payload: BacktestRunRequest,
        *,
        control_callback: Callable[[], None] | None = None,
    ) -> list[BacktestResponse]:
        out: list[BacktestResponse] = []
        runtime_stage_callback = self._get_backtest_runtime_stage_timing_callback()
        self._set_backtest_runtime_stage_timing_callback(None)
        try:
            for probe_payload in self._build_backtest_neighbor_payloads(payload):
                try:
                    if control_callback is not None:
                        control_callback()
                    probe_result = self.run_backtest(
                        probe_payload,
                        control_callback=control_callback,
                    )
                    out.append(probe_result)
                except Exception:
                    continue
        finally:
            self._set_backtest_runtime_stage_timing_callback(runtime_stage_callback)
        return out

    def _compute_backtest_stability_diagnostics(
        self,
        result: BacktestResponse,
        *,
        neighbor_results: list[BacktestResponse],
    ) -> BacktestStabilityDiagnostics:
        trade_count = int(result.stats.trade_count)
        threshold = 20
        trade_count_penalty = 0.0
        if trade_count < threshold:
            trade_count_penalty = min(1.0, float(threshold - trade_count) / float(threshold)) * 0.35

        monthly_returns = [
            float(row.return_ratio)
            for row in result.monthly_returns
            if math.isfinite(float(row.return_ratio))
        ]
        monthly_std = float(np.std(monthly_returns)) if len(monthly_returns) >= 2 else 0.0
        return_variance_penalty = min(0.35, max(0.0, monthly_std) * 3.0)

        neighborhood_consistency = 0.5
        if neighbor_results:
            base_score = float(self._backtest_plateau_score(result))
            neighbor_scores = [float(self._backtest_plateau_score(item)) for item in neighbor_results]
            close_band = max(0.08, abs(base_score) * 0.25)
            close_ratio = sum(1 for item in neighbor_scores if abs(item - base_score) <= close_band) / len(neighbor_scores)
            positive_ratio = sum(1 for item in neighbor_scores if item > 0.0) / len(neighbor_scores)
            neighborhood_consistency = (close_ratio + positive_ratio) / 2.0

        stability_score = (
            0.45 * neighborhood_consistency
            + 0.30 * (1.0 - trade_count_penalty)
            + 0.25 * (1.0 - return_variance_penalty)
        )
        stability_score = max(0.0, min(1.0, stability_score))

        notes: list[str] = []
        if trade_count < threshold:
            notes.append(f"交易数 {trade_count} 低于稳定性阈值 {threshold}，已施加惩罚。")
        if return_variance_penalty > 0.0:
            notes.append(f"月度收益标准差 {monthly_std:.4f}，已施加波动惩罚。")
        if neighbor_results:
            notes.append(f"邻域一致性 {neighborhood_consistency:.3f}（基于 {len(neighbor_results)} 组邻域参数）。")
        else:
            notes.append("邻域一致性未执行，使用中性评分。")

        return BacktestStabilityDiagnostics(
            stability_score=round(float(stability_score), 6),
            min_trade_count_threshold=int(threshold),
            trade_count_penalty=round(float(trade_count_penalty), 6),
            neighborhood_consistency=round(float(neighborhood_consistency), 6),
            return_variance_penalty=round(float(return_variance_penalty), 6),
            monthly_return_std=round(float(monthly_std), 6),
            notes=notes,
        )

    def _build_walk_forward_fold_ranges(
        self,
        payload: BacktestRunRequest,
    ) -> list[tuple[str, str, str, str]]:
        scan_dates = self._build_backtest_scan_dates(payload.date_from, payload.date_to)
        if len(scan_dates) < 90:
            return []
        split1 = len(scan_dates) // 3
        split2 = (len(scan_dates) * 2) // 3
        seg1 = scan_dates[:split1]
        seg2 = scan_dates[split1:split2]
        seg3 = scan_dates[split2:]
        if len(seg1) < 20 or len(seg2) < 20 or len(seg3) < 20:
            return []
        return [
            (seg1[0], seg1[-1], seg2[0], seg2[-1]),
            (seg1[0], seg2[-1], seg3[0], seg3[-1]),
        ]

    def _build_walk_forward_candidate_payloads(self, payload: BacktestRunRequest) -> list[BacktestRunRequest]:
        base = payload.model_copy(update={"enable_advanced_analysis": False}, deep=True)
        neighbors = self._build_backtest_neighbor_payloads(payload)
        out = [base]
        out.extend(neighbors[:3])
        deduped: list[BacktestRunRequest] = []
        seen: set[tuple[float, ...]] = set()
        for item in out:
            key = (
                float(item.window_days),
                float(item.min_score),
                float(item.stop_loss),
                float(item.take_profit),
                float(item.max_positions),
                float(item.position_pct),
                float(item.max_symbols),
                float(item.priority_topk_per_day),
            )
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        return deduped

    def _run_backtest_walk_forward(
        self,
        payload: BacktestRunRequest,
        *,
        control_callback: Callable[[], None] | None = None,
    ) -> BacktestWalkForwardReport:
        fold_ranges = self._build_walk_forward_fold_ranges(payload)
        candidates = self._build_walk_forward_candidate_payloads(payload)
        if not fold_ranges:
            return BacktestWalkForwardReport(
                fold_count=0,
                candidate_count=len(candidates),
                notes=["walk-forward 未执行：区间过短，至少需要约 90 个交易日。"],
            )
        if len(candidates) <= 0:
            return BacktestWalkForwardReport(
                fold_count=0,
                candidate_count=0,
                notes=["walk-forward 未执行：候选参数为空。"],
            )

        folds: list[BacktestWalkForwardFold] = []
        notes: list[str] = []
        runtime_stage_callback = self._get_backtest_runtime_stage_timing_callback()
        self._set_backtest_runtime_stage_timing_callback(None)
        try:
            for fold_idx, (train_from, train_to, test_from, test_to) in enumerate(fold_ranges, start=1):
                best_payload: BacktestRunRequest | None = None
                best_train_result: BacktestResponse | None = None
                best_train_score = -float("inf")
                for candidate in candidates:
                    train_payload = candidate.model_copy(
                        update={
                            "date_from": train_from,
                            "date_to": train_to,
                            "enable_advanced_analysis": False,
                        },
                        deep=True,
                    )
                    try:
                        if control_callback is not None:
                            control_callback()
                        train_result = self.run_backtest(
                            train_payload,
                            control_callback=control_callback,
                        )
                    except Exception as exc:  # noqa: BLE001
                        notes.append(f"walk-forward fold#{fold_idx} 训练候选失败：{exc}")
                        continue
                    train_score = float(self._backtest_plateau_score(train_result))
                    if train_score > best_train_score:
                        best_train_score = train_score
                        best_payload = train_payload
                        best_train_result = train_result

                if best_payload is None or best_train_result is None:
                    notes.append(f"walk-forward fold#{fold_idx} 未找到可用训练参数。")
                    continue

                test_payload = best_payload.model_copy(
                    update={
                        "date_from": test_from,
                        "date_to": test_to,
                        "enable_advanced_analysis": False,
                    },
                    deep=True,
                )
                try:
                    if control_callback is not None:
                        control_callback()
                    test_result = self.run_backtest(
                        test_payload,
                        control_callback=control_callback,
                    )
                except Exception as exc:  # noqa: BLE001
                    notes.append(f"walk-forward fold#{fold_idx} 测试阶段失败：{exc}")
                    continue

                folds.append(
                    BacktestWalkForwardFold(
                        fold_index=int(fold_idx),
                        train_date_from=str(train_from),
                        train_date_to=str(train_to),
                        test_date_from=str(test_from),
                        test_date_to=str(test_to),
                        selected_params=self._plateau_params_from_payload(best_payload),
                        train_score=round(float(best_train_score), 6),
                        test_score=round(float(self._backtest_plateau_score(test_result)), 6),
                        train_stats=best_train_result.stats,
                        test_stats=test_result.stats,
                    )
                )

            if not folds:
                return BacktestWalkForwardReport(
                    fold_count=0,
                    candidate_count=len(candidates),
                    notes=notes or ["walk-forward 未生成有效折叠。"],
                )

            pass_count = sum(
                1
                for fold in folds
                if float(fold.test_stats.total_return) > 0.0 and float(fold.test_stats.win_rate) >= 0.45
            )
            avg_test_return = self._safe_mean([float(fold.test_stats.total_return) for fold in folds])
            avg_test_win_rate = self._safe_mean([float(fold.test_stats.win_rate) for fold in folds])
            return BacktestWalkForwardReport(
                fold_count=len(folds),
                candidate_count=len(candidates),
                oos_pass_rate=round(float(pass_count / len(folds)), 6),
                avg_test_return=round(float(avg_test_return), 6),
                avg_test_win_rate=round(float(avg_test_win_rate), 6),
                folds=folds,
                notes=notes,
            )
        finally:
            self._set_backtest_runtime_stage_timing_callback(runtime_stage_callback)

    def _enrich_backtest_advanced_analysis(
        self,
        *,
        payload: BacktestRunRequest,
        result: BacktestResponse,
        control_callback: Callable[[], None] | None = None,
        analysis_progress_callback: Callable[[str], None] | None = None,
    ) -> BacktestResponse:
        advanced_start_ts = time.perf_counter()

        def _mark_progress(message: str) -> None:
            if analysis_progress_callback is None:
                return
            analysis_progress_callback(message)

        risk_start_ts = time.perf_counter()
        risk_metrics = self._compute_backtest_risk_metrics(payload, result)
        self._emit_backtest_runtime_stage_timing(
            "advanced_risk_metrics",
            "高级分析-风险指标",
            time.perf_counter() - risk_start_ts,
        )
        _mark_progress("高级分析：风险指标计算完成。")

        regime_start_ts = time.perf_counter()
        regime_breakdown = self._compute_backtest_regime_breakdown(payload, result)
        self._emit_backtest_runtime_stage_timing(
            "advanced_regime_breakdown",
            "高级分析-市场状态拆分",
            time.perf_counter() - regime_start_ts,
        )
        _mark_progress("高级分析：市场状态拆分完成。")

        monte_start_ts = time.perf_counter()
        monte_carlo = self._compute_backtest_monte_carlo(result)
        self._emit_backtest_runtime_stage_timing(
            "advanced_monte_carlo",
            "高级分析-蒙特卡洛",
            time.perf_counter() - monte_start_ts,
        )
        _mark_progress("高级分析：蒙特卡洛压力测试完成。")

        scan_dates = self._build_backtest_scan_dates(payload.date_from, payload.date_to)
        neighbor_results: list[BacktestResponse] = []
        walk_forward: BacktestWalkForwardReport | None = None
        extra_notes: list[str] = []
        if len(scan_dates) >= 120 and int(result.stats.trade_count) >= 12:
            neighbor_start_ts = time.perf_counter()
            neighbor_results = self._run_backtest_neighborhood_probe(payload, control_callback=control_callback)
            self._emit_backtest_runtime_stage_timing(
                "advanced_neighbor_probe",
                "高级分析-邻域稳定性探测",
                time.perf_counter() - neighbor_start_ts,
            )
            _mark_progress("高级分析：邻域稳定性探测完成。")

            walk_forward_start_ts = time.perf_counter()
            walk_forward = self._run_backtest_walk_forward(payload, control_callback=control_callback)
            self._emit_backtest_runtime_stage_timing(
                "advanced_walk_forward",
                "高级分析-Walk-forward",
                time.perf_counter() - walk_forward_start_ts,
            )
            _mark_progress("高级分析：Walk-forward 验证完成。")
        else:
            extra_notes.append("高级分析已降级：区间或交易数不足，未执行 walk-forward 与邻域稳定性探测。")
            _mark_progress("高级分析：邻域稳定性探测已跳过（样本不足）。")
            _mark_progress("高级分析：Walk-forward 已跳过（样本不足）。")

        stability_start_ts = time.perf_counter()
        stability = self._compute_backtest_stability_diagnostics(
            result,
            neighbor_results=neighbor_results,
        )
        self._emit_backtest_runtime_stage_timing(
            "advanced_stability",
            "高级分析-稳定性评分",
            time.perf_counter() - stability_start_ts,
        )
        _mark_progress("高级分析：稳定性评分完成。")
        notes = list(result.notes)
        notes.append(
            f"风险指标: Sharpe={risk_metrics.sharpe:.3f}, Sortino={risk_metrics.sortino:.3f}, Calmar={risk_metrics.calmar:.3f}。"
        )
        notes.append(
            f"稳定性评分={stability.stability_score:.3f}（邻域一致性={stability.neighborhood_consistency:.3f}，交易数惩罚={stability.trade_count_penalty:.3f}，方差惩罚={stability.return_variance_penalty:.3f}）。"
        )
        if walk_forward is not None and walk_forward.fold_count > 0:
            notes.append(
                f"Walk-forward: folds={walk_forward.fold_count}, 候选参数={walk_forward.candidate_count}, OOS通过率={walk_forward.oos_pass_rate:.1%}。"
            )
        for item in extra_notes:
            notes.append(item)
        _mark_progress("高级分析：汇总完成。")
        self._emit_backtest_runtime_stage_timing(
            "advanced_total",
            "高级分析总耗时",
            time.perf_counter() - advanced_start_ts,
        )

        return result.model_copy(
            update={
                "risk_metrics": risk_metrics,
                "stability_diagnostics": stability,
                "regime_breakdown": regime_breakdown,
                "monte_carlo": monte_carlo,
                "walk_forward": walk_forward,
                "notes": notes,
            }
        )

    def run_backtest(
        self,
        payload: BacktestRunRequest,
        *,
        progress_callback: Callable[[str, int, int, str], None] | None = None,
        control_callback: Callable[[], None] | None = None,
        prebuilt_universe: tuple[list[str], dict[str, set[str]] | None, list[str]] | None = None,
        feature_cache: tuple[Callable, Callable] | None = None,
    ) -> BacktestResponse:
        payload, active_event_profile, active_event_profile_id, active_event_profile_hash = (
            self._bind_backtest_event_judgment_profile(payload)
        )
        with self._backtest_runtime_event_judgment_binding(
            profile=active_event_profile,
            profile_id=active_event_profile_id,
            profile_hash=active_event_profile_hash,
        ):
            return self._run_backtest_bound(
                payload,
                progress_callback=progress_callback,
                control_callback=control_callback,
                prebuilt_universe=prebuilt_universe,
                feature_cache=feature_cache,
            )

    def _run_backtest_bound(
        self,
        payload: BacktestRunRequest,
        *,
        progress_callback: Callable[[str, int, int, str], None] | None = None,
        control_callback: Callable[[], None] | None = None,
        prebuilt_universe: tuple[list[str], dict[str, set[str]] | None, list[str]] | None = None,
        feature_cache: tuple[Callable, Callable] | None = None,
    ) -> BacktestResponse:
        strategy_meta, normalized_strategy_params, strategy_params_hash = self._resolve_strategy_runtime(
            strategy_id=payload.strategy_id,
            strategy_params=payload.strategy_params,
        )
        payload = self._apply_strategy_overrides_to_backtest_payload(
            payload,
            strategy_id=str(strategy_meta["strategy_id"]),
            normalized_strategy_params=normalized_strategy_params,
        )
        payload = self._strategy_registry.entry_policy(
            strategy_id=str(strategy_meta["strategy_id"]),
            payload=payload,
            params=normalized_strategy_params,
        )
        payload = self._strategy_registry.exit_policy(
            strategy_id=str(strategy_meta["strategy_id"]),
            payload=payload,
            params=normalized_strategy_params,
        )
        from .core.backtest_engine import EVENT_INDEPENDENT_STRATEGY_IDS

        normalized_sid = str(strategy_meta.get("strategy_id", "")).strip().lower()
        cleared_wyckoff_entry_config = False
        if normalized_sid in EVENT_INDEPENDENT_STRATEGY_IDS and (
            payload.entry_events
            or int(payload.min_event_count) > 0
            or bool(payload.require_sequence)
        ):
            cleared_wyckoff_entry_config = True
            payload = payload.model_copy(
                update={
                    "entry_events": [],
                    "min_event_count": 0,
                    "require_sequence": False,
                }
            )
        strategy_note = self._build_strategy_snapshot_note(
            strategy_meta=strategy_meta,
            strategy_params_hash=strategy_params_hash,
            strategy_params=normalized_strategy_params,
        )
        active_event_profile = (
            dict(payload.event_judgment_profile_snapshot)
            if isinstance(payload.event_judgment_profile_snapshot, dict)
            else self._active_event_judgment_profile()
        )
        active_event_profile_id = (
            str(payload.event_judgment_profile_id or "").strip()
            or str(active_event_profile.get("profile_id", "")).strip()
            or self._default_event_judgment_profile_id()
        )
        active_event_profile_hash = (
            str(payload.event_judgment_profile_hash or "").strip()
            or self._event_judgment_profile_hash(active_event_profile)
        )
        strategy_capabilities = strategy_meta.get("capabilities", {}) if isinstance(strategy_meta, dict) else {}
        strategy_supports_matrix = bool(strategy_capabilities.get("supports_matrix", False))
        strategy_supports_entry_delay = bool(strategy_capabilities.get("supports_entry_delay", True))
        capability_notes: list[str] = [
            f"事件判别模板: id={active_event_profile_id}, hash={active_event_profile_hash}"
        ]
        if cleared_wyckoff_entry_config:
            capability_notes.append(
                f"策略 {strategy_meta.get('strategy_id')} 使用内置信号入场，已忽略威科夫入场事件/事件数/顺序配置。"
            )
        requested_entry_delay_days = int(payload.entry_delay_days)
        if (not strategy_supports_entry_delay) and requested_entry_delay_days != 1:
            payload = payload.model_copy(update={"entry_delay_days": 1, "delay_invalidation_enabled": False})
            capability_notes.append(
                f"策略 {strategy_meta.get('strategy_id')} 不支持延迟入场，已强制 entry_delay_days=1。"
            )
        board_filters = [item for item in payload.board_filters if item in {"main", "gem", "star", "beijing", "st"}]
        if control_callback is not None:
            control_callback()
        effective_progress_callback = progress_callback
        if control_callback is not None:

            def _progress_with_control(current_date: str, processed_dates: int, total: int, message: str) -> None:
                control_callback()
                if progress_callback is not None:
                    progress_callback(current_date, processed_dates, total, message)

            effective_progress_callback = _progress_with_control

        progress_state = {"processed": 0, "total": 0}

        def _emit_progress(current_date: str, processed_dates: int, total: int, message: str) -> None:
            processed_safe = max(0, int(processed_dates))
            total_safe = max(1, int(total))
            progress_state["processed"] = max(int(progress_state["processed"]), processed_safe)
            progress_state["total"] = max(int(progress_state["total"]), total_safe)
            if effective_progress_callback is not None:
                effective_progress_callback(current_date, processed_safe, total_safe, message)

        advanced_slots_total = self._estimate_backtest_advanced_progress_slots(payload)
        advanced_base_processed = 0
        advanced_done_slots = 0

        def _start_advanced_progress() -> None:
            nonlocal advanced_base_processed, advanced_done_slots
            advanced_base_processed = max(int(progress_state["processed"]), 0)
            advanced_done_slots = 0
            target_total = max(int(progress_state["total"]), advanced_base_processed + advanced_slots_total)
            _emit_progress(
                payload.date_to,
                advanced_base_processed,
                max(1, target_total),
                "主回测完成，正在生成高级分析报告...",
            )

        def _advance_advanced_progress(message: str) -> None:
            nonlocal advanced_done_slots
            if advanced_slots_total <= 0:
                return
            advanced_done_slots = min(advanced_slots_total, advanced_done_slots + 1)
            target_total = max(int(progress_state["total"]), advanced_base_processed + advanced_slots_total)
            _emit_progress(
                payload.date_to,
                advanced_base_processed + advanced_done_slots,
                max(1, target_total),
                message,
            )

        def _finish_advanced_progress() -> None:
            remaining = max(0, advanced_slots_total - advanced_done_slots)
            for _ in range(remaining):
                _advance_advanced_progress("高级分析：正在汇总结果...")
            _emit_progress(
                payload.date_to,
                max(int(progress_state["processed"]), advanced_base_processed + advanced_slots_total),
                max(int(progress_state["total"]), advanced_base_processed + advanced_slots_total, 1),
                "高级分析完成，正在汇总结果...",
            )

        cached_result = self._load_backtest_result_cache(
            payload,
            strategy_version=str(strategy_meta.get("version") or "1.0.0"),
            strategy_params_hash=str(strategy_params_hash),
            event_judgment_profile_hash=active_event_profile_hash,
        )
        if cached_result is not None:
            _emit_progress(payload.date_to, 1, 1, "回测结果缓存命中，直接返回。")
            cached_notes = list(cached_result.notes)
            for note in capability_notes:
                if note not in cached_notes:
                    cached_notes.insert(0, note)
            cache_note = "回测结果缓存命中：复用本地持久化结果。"
            if cache_note not in cached_notes:
                cached_notes.insert(0, cache_note)
            if cached_notes != cached_result.notes:
                cached_result = cached_result.model_copy(update={"notes": cached_notes})
            if payload.enable_advanced_analysis and (
                cached_result.risk_metrics is None
                or cached_result.stability_diagnostics is None
                or cached_result.monte_carlo is None
            ):
                try:
                    _start_advanced_progress()
                    cached_result = self._enrich_backtest_advanced_analysis(
                        payload=payload,
                        result=cached_result,
                        control_callback=control_callback,
                        analysis_progress_callback=(
                            _advance_advanced_progress if advanced_slots_total > 0 else None
                        ),
                    )
                    _finish_advanced_progress()
                    self._save_backtest_result_cache(
                        payload,
                        cached_result,
                        strategy_version=str(strategy_meta.get("version") or "1.0.0"),
                        strategy_params_hash=str(strategy_params_hash),
                        event_judgment_profile_hash=active_event_profile_hash,
                    )
                except Exception as exc:  # noqa: BLE001
                    notes = list(cached_result.notes)
                    notes.append(f"高级分析失败，已返回主回测结果：{exc}")
                    cached_result = cached_result.model_copy(update={"notes": notes})
            cached_result = self._apply_strategy_metadata_to_backtest_result(
                cached_result,
                strategy_meta=strategy_meta,
                strategy_params=normalized_strategy_params,
                strategy_params_hash=strategy_params_hash,
                strategy_note=strategy_note,
                execution_path=None,
                effective_payload=payload,
            )
            return cached_result

        matrix_fallback_note: str | None = None
        execution_path_preference = str(payload.execution_path_preference or "auto").strip().lower()
        if execution_path_preference not in {"auto", "matrix", "legacy"}:
            execution_path_preference = "auto"
            payload = payload.model_copy(update={"execution_path_preference": "auto"})
        if payload.mode == "trend_pool" and execution_path_preference == "auto":
            # 矩阵信号策略本身就是矩阵逻辑，不应降级到 legacy
            if str(strategy_meta.get("strategy_id", "")).strip() != "matrix_signal_v1":
                execution_path_preference = "legacy"
                payload = payload.model_copy(update={"execution_path_preference": "legacy"})
        force_matrix_path = execution_path_preference == "matrix"
        force_legacy_path = execution_path_preference == "legacy"
        matrix_supported = (
            payload.mode in {"full_market", "trend_pool"}
            and payload.pool_roll_mode in {"daily", "weekly", "position"}
        )
        matrix_engine_enabled = bool(self._is_backtest_matrix_engine_enabled())
        can_try_matrix = matrix_engine_enabled and matrix_supported and strategy_supports_matrix and (not force_legacy_path)
        if can_try_matrix:
            try:
                matrix_result = self._run_backtest_matrix(
                    payload=payload,
                    board_filters=board_filters,
                    progress_callback=_emit_progress,
                    control_callback=control_callback,
                )
                guard_note: str | None = None
                guard_enabled = self._is_backtest_matrix_diff_guard_enabled() and (not force_matrix_path)
                if guard_enabled:
                    try:
                        legacy_shadow_result = self._run_backtest_legacy_shadow_for_matrix_diff_guard(payload=payload)
                        should_fallback, guard_note = self._evaluate_backtest_matrix_legacy_diff(
                            matrix_result=matrix_result,
                            legacy_result=legacy_shadow_result,
                        )
                        if should_fallback:
                            fallback_notes = self._strip_forced_legacy_note(list(legacy_shadow_result.notes))
                            fallback_guard_note = f"矩阵/旧路径偏差超阈值，已自动回退旧路径。{guard_note}"
                            if fallback_guard_note not in fallback_notes:
                                fallback_notes.insert(0, fallback_guard_note)
                            fallback_result = legacy_shadow_result.model_copy(
                                update={
                                    "notes": fallback_notes,
                                    "execution_path": "legacy",
                                }
                            )
                            if payload.enable_advanced_analysis and (
                                fallback_result.risk_metrics is None
                                or fallback_result.stability_diagnostics is None
                                or fallback_result.monte_carlo is None
                            ):
                                try:
                                    _start_advanced_progress()
                                    fallback_result = self._enrich_backtest_advanced_analysis(
                                        payload=payload,
                                        result=fallback_result,
                                        control_callback=control_callback,
                                        analysis_progress_callback=(
                                            _advance_advanced_progress if advanced_slots_total > 0 else None
                                        ),
                                    )
                                    _finish_advanced_progress()
                                except Exception as exc:  # noqa: BLE001
                                    notes = list(fallback_result.notes)
                                    notes.append(f"高级分析失败，已返回主回测结果：{exc}")
                                    fallback_result = fallback_result.model_copy(update={"notes": notes})
                            if capability_notes:
                                notes = list(fallback_result.notes)
                                for note in capability_notes:
                                    if note not in notes:
                                        notes.insert(0, note)
                                if notes != fallback_result.notes:
                                    fallback_result = fallback_result.model_copy(update={"notes": notes})
                            fallback_result = self._apply_strategy_metadata_to_backtest_result(
                                fallback_result,
                                strategy_meta=strategy_meta,
                                strategy_params=normalized_strategy_params,
                                strategy_params_hash=strategy_params_hash,
                                strategy_note=strategy_note,
                                execution_path="legacy",
                                effective_payload=payload,
                            )
                            self._save_backtest_result_cache(
                                payload,
                                fallback_result,
                                strategy_version=str(strategy_meta.get("version") or "1.0.0"),
                                strategy_params_hash=str(strategy_params_hash),
                                event_judgment_profile_hash=active_event_profile_hash,
                            )
                            return fallback_result
                    except Exception as exc:  # noqa: BLE001
                        guard_note = f"矩阵偏差守卫执行失败，已保留矩阵结果：{exc}"
                if guard_note:
                    notes = list(matrix_result.notes)
                    if guard_note not in notes:
                        notes.insert(0, guard_note)
                    if notes != matrix_result.notes:
                        matrix_result = matrix_result.model_copy(update={"notes": notes})
                if payload.enable_advanced_analysis:
                    try:
                        _start_advanced_progress()
                        matrix_result = self._enrich_backtest_advanced_analysis(
                            payload=payload,
                            result=matrix_result,
                            control_callback=control_callback,
                            analysis_progress_callback=(
                                _advance_advanced_progress if advanced_slots_total > 0 else None
                            ),
                        )
                        _finish_advanced_progress()
                    except Exception as exc:  # noqa: BLE001
                        notes = list(matrix_result.notes)
                        notes.append(f"高级分析失败，已返回主回测结果：{exc}")
                        matrix_result = matrix_result.model_copy(update={"notes": notes})
                if capability_notes:
                    notes = list(matrix_result.notes)
                    for note in capability_notes:
                        if note not in notes:
                            notes.insert(0, note)
                    if notes != matrix_result.notes:
                        matrix_result = matrix_result.model_copy(update={"notes": notes})
                matrix_result = self._apply_strategy_metadata_to_backtest_result(
                    matrix_result,
                    strategy_meta=strategy_meta,
                    strategy_params=normalized_strategy_params,
                    strategy_params_hash=strategy_params_hash,
                    strategy_note=strategy_note,
                    execution_path="matrix",
                    effective_payload=payload,
                )
                self._save_backtest_result_cache(
                    payload,
                    matrix_result,
                    strategy_version=str(strategy_meta.get("version") or "1.0.0"),
                    strategy_params_hash=str(strategy_params_hash),
                    event_judgment_profile_hash=active_event_profile_hash,
                )
                return matrix_result
            except BacktestTaskCancelledError:
                raise
            except Exception as exc:
                matrix_fallback_note = (
                    f"矩阵引擎执行失败（pref={execution_path_preference}），已回退旧路径：{exc}"
                )
        else:
            if force_legacy_path:
                matrix_fallback_note = "已按参数强制使用旧路径执行（execution_path_preference=legacy）。"
            elif force_matrix_path:
                reason_parts: list[str] = []
                if not matrix_engine_enabled:
                    reason_parts.append("矩阵引擎未启用")
                if not matrix_supported:
                    reason_parts.append("当前模式不支持矩阵路径")
                if not strategy_supports_matrix:
                    reason_parts.append(f"策略 {strategy_meta.get('strategy_id')} 不支持矩阵执行")
                reason_text = "，".join(reason_parts) if reason_parts else "未知原因"
                matrix_fallback_note = (
                    f"已请求矩阵路径（execution_path_preference=matrix），但{reason_text}，已回退旧路径。"
                )
            elif (not strategy_supports_matrix) and matrix_supported:
                matrix_fallback_note = (
                    f"策略 {strategy_meta.get('strategy_id')} 不支持矩阵执行，已回退旧路径。"
                )

        degraded_reason: str | None = None
        resolved_run_id: str | None = None
        trend_pool_run: ScreenerRunDetail | None = None
        if payload.mode == "trend_pool":
            trend_pool_run = self._require_backtest_trend_pool_run(payload.run_id)
            resolved_run_id = trend_pool_run.run_id
            degraded_reason = trend_pool_run.degraded_reason if trend_pool_run.degraded else None

        pool_notes: list[str] = []
        if capability_notes:
            pool_notes.extend(capability_notes)
        if matrix_fallback_note:
            pool_notes.append(matrix_fallback_note)

        symbols: list[str] = []
        allowed_symbols_by_date: dict[str, set[str]] | None = None
        scan_dates = self._build_backtest_scan_dates(payload.date_from, payload.date_to)
        rolling_start_ts = time.perf_counter()

        engine = BacktestEngine(
            get_candles=self._ensure_candles,
            build_row=feature_cache[0] if feature_cache else self._build_row_from_candles,
            calc_snapshot=feature_cache[1] if feature_cache else lambda row, window_days, as_of_date: self._calc_wyckoff_snapshot(
                row,
                window_days=window_days,
                as_of_date=as_of_date,
            ),
            resolve_symbol_name=self._resolve_symbol_name,
            strategy_signal_filter=lambda strategy_id, row, snapshot, params: self._strategy_registry.generate_signals(
                strategy_id=strategy_id,
                row=row,
                snapshot=snapshot,
                params=params,
            ),
            strategy_signal_context_builder=lambda strategy_id, snapshot, params: self._build_strategy_signal_context(
                strategy_id,
                snapshot,
                params,
            ),
        )

        if prebuilt_universe is not None:
            symbols, allowed_symbols_by_date, prebuilt_notes = prebuilt_universe
            pool_notes = [*pool_notes, *prebuilt_notes]
        elif payload.mode == "trend_pool":
            if trend_pool_run is None:
                raise ValueError("趋势池筛选任务不可用。")
            trend_pool_params = self._bind_step_configs_to_screener_params(
                trend_pool_run.params,
                trend_pool_run.step_configs,
            )
            if payload.pool_roll_mode == "position":
                if not scan_dates:
                    raise ValueError("回测区间内无可扫描交易日。")
                seed_refresh_dates = [scan_dates[0]]
                seed_symbols, seed_allowed_by_date, _, _, _ = self._build_trend_pool_rolling_universe(
                    payload=payload,
                    screener_params=trend_pool_params,
                    board_filters=board_filters,
                    refresh_dates=seed_refresh_dates,
                    progress_callback=None,
                )
                if not seed_symbols:
                    raise ValueError("回测股票池为空：持仓触发滚动初始池为空。")
                probe_result = engine.run(
                    payload=payload,
                    symbols=seed_symbols,
                    allowed_symbols_by_date=seed_allowed_by_date,
                    control_callback=control_callback,
                )
                refresh_date_set = self._collect_position_mode_refresh_dates(
                    scan_dates,
                    seed_date=scan_dates[0],
                    exit_dates=[trade.exit_date for trade in probe_result.trades],
                )
                _emit_progress(
                    scan_dates[0],
                    0,
                    max(1, len(refresh_date_set)),
                    "持仓触发滚动：正在根据卖出日生成刷新计划...",
                )
                rolling_symbols, rolling_allowed_by_date, notes, _, refresh_dates_used = (
                    self._build_trend_pool_rolling_universe(
                        payload=payload,
                        screener_params=trend_pool_params,
                        board_filters=board_filters,
                        refresh_dates=refresh_date_set,
                        progress_callback=_emit_progress,
                    )
                )
                pool_notes = [
                    *pool_notes,
                    *notes,
                    f"持仓触发滚动：首日+卖出信号日/卖出后下一交易日刷新，共 {len(refresh_dates_used)} 次。",
                ]
            else:
                rolling_symbols, rolling_allowed_by_date, notes, _, _ = self._build_trend_pool_rolling_universe(
                    payload=payload,
                    screener_params=trend_pool_params,
                    board_filters=board_filters,
                    refresh_dates=None,
                    progress_callback=_emit_progress,
                )
                pool_notes = [*pool_notes, *notes]
            if not rolling_symbols:
                reason_text = "；".join(pool_notes) if pool_notes else "滚动筛选结果为空。"
                raise ValueError(f"回测股票池为空：{reason_text}")
            symbols = rolling_symbols
            allowed_symbols_by_date = rolling_allowed_by_date
        elif payload.mode == "full_market":
            if payload.pool_roll_mode == "position":
                if not scan_dates:
                    raise ValueError("回测区间内无可扫描交易日。")
                seed_refresh_dates = [scan_dates[0]]
                seed_symbols, seed_allowed_by_date, _, _, _ = self._build_full_market_rolling_universe(
                    payload=payload,
                    board_filters=board_filters,
                    refresh_dates=seed_refresh_dates,
                    progress_callback=None,
                )
                if not seed_symbols:
                    raise ValueError("回测股票池为空：持仓触发滚动初始池为空。")
                probe_result = engine.run(
                    payload=payload,
                    symbols=seed_symbols,
                    allowed_symbols_by_date=seed_allowed_by_date,
                    control_callback=control_callback,
                )
                refresh_date_set = self._collect_position_mode_refresh_dates(
                    scan_dates,
                    seed_date=scan_dates[0],
                    exit_dates=[trade.exit_date for trade in probe_result.trades],
                )
                _emit_progress(
                    scan_dates[0],
                    0,
                    max(1, len(refresh_date_set)),
                    "持仓触发滚动：正在根据卖出日生成刷新计划...",
                )
                rolling_symbols, rolling_allowed_by_date, notes, _, refresh_dates_used = (
                    self._build_full_market_rolling_universe(
                        payload=payload,
                        board_filters=board_filters,
                        refresh_dates=refresh_date_set,
                        progress_callback=_emit_progress,
                    )
                )
                pool_notes = [
                    *pool_notes,
                    *notes,
                    f"持仓触发滚动：首日+卖出信号日/卖出后下一交易日刷新，共 {len(refresh_dates_used)} 次。",
                ]
            else:
                rolling_symbols, rolling_allowed_by_date, notes, _, _ = self._build_full_market_rolling_universe(
                    payload=payload,
                    board_filters=board_filters,
                    refresh_dates=None,
                    progress_callback=_emit_progress,
                )
                pool_notes = [*pool_notes, *notes]
            if not rolling_symbols:
                reason_text = "；".join(pool_notes) if pool_notes else "滚动筛选结果为空。"
                raise ValueError(f"回测股票池为空：{reason_text}")
            symbols = rolling_symbols
            allowed_symbols_by_date = rolling_allowed_by_date
        else:
            raise ValueError(f"不支持的回测模式: {payload.mode}")

        self._emit_backtest_runtime_stage_timing(
            "rolling_universe",
            "候选池构建",
            time.perf_counter() - rolling_start_ts,
        )

        if not symbols:
            raise ValueError("回测股票池为空，请先执行筛选或调整回测模式")

        self._validate_backtest_data_coverage(
            payload,
            symbols,
            scope_label="滚动池并集",
        )

        main_base_processed = max(int(progress_state["processed"]), 1)
        _emit_progress(
            payload.date_to,
            main_base_processed,
            main_base_processed + 1,
            "候选池构建完成，开始执行主回测...",
        )
        execute_start_ts = time.perf_counter()
        result = engine.run(
            payload=payload,
            symbols=symbols,
            allowed_symbols_by_date=allowed_symbols_by_date,
            control_callback=control_callback,
        )
        execute_elapsed = time.perf_counter() - execute_start_ts
        self._emit_backtest_runtime_stage_timing("execution_match", "主回测执行", execute_elapsed)
        _emit_progress(
            payload.date_to,
            main_base_processed + 1,
            main_base_processed + 1,
            "主回测完成，正在汇总结果...",
        )

        notes = list(result.notes)
        if pool_notes:
            notes = [*pool_notes, *notes]
        if board_filters:
            roll_mode_label = {
                "daily": "每日滚动",
                "weekly": "每周滚动",
                "position": "持仓触发滚动",
            }.get(payload.pool_roll_mode, "每日滚动")
            notes.insert(0, f"候选池板块过滤: {','.join(board_filters)}（{roll_mode_label}生效）")
        if payload.mode == "trend_pool" and resolved_run_id:
            notes.insert(0, f"使用筛选任务: {resolved_run_id}")
        notes.insert(
            0,
            self._build_backtest_param_snapshot_note(
                payload,
                resolved_run_id=resolved_run_id,
                board_filters=board_filters,
            ),
        )
        if degraded_reason:
            notes.append(f"候选池降级原因: {degraded_reason}")
        if notes != result.notes:
            result = result.model_copy(update={"notes": notes})
        if payload.enable_advanced_analysis:
            try:
                _start_advanced_progress()
                result = self._enrich_backtest_advanced_analysis(
                    payload=payload,
                    result=result,
                    control_callback=control_callback,
                    analysis_progress_callback=(
                        _advance_advanced_progress if advanced_slots_total > 0 else None
                    ),
                )
                _finish_advanced_progress()
            except Exception as exc:  # noqa: BLE001
                notes = list(result.notes)
                notes.append(f"高级分析失败，已返回主回测结果：{exc}")
                result = result.model_copy(update={"notes": notes})
        result = self._apply_strategy_metadata_to_backtest_result(
            result,
            strategy_meta=strategy_meta,
            strategy_params=normalized_strategy_params,
            strategy_params_hash=strategy_params_hash,
            strategy_note=strategy_note,
            execution_path="legacy",
            effective_payload=payload,
        )
        self._save_backtest_result_cache(
            payload,
            result,
            strategy_version=str(strategy_meta.get("version") or "1.0.0"),
            strategy_params_hash=str(strategy_params_hash),
            event_judgment_profile_hash=active_event_profile_hash,
        )
        return result

    def _estimate_backtest_progress_total_dates(self, payload: BacktestRunRequest) -> int:
        scan_total = self._estimate_backtest_scan_progress_total_dates(payload)
        # 额外预留 1 格用于“主回测执行”阶段，避免滚动扫描结束即 100%。
        post_scan_slots = 1
        advanced_slots = self._estimate_backtest_advanced_progress_slots(payload)
        return max(1, int(scan_total) + int(post_scan_slots) + int(advanced_slots))

    def _build_backtest_task_state_payload(self) -> dict[str, object]:
        with self._backtest_task_lock:
            tasks = []
            for task_id, task in self._backtest_tasks.items():
                payload = self._backtest_task_payloads.get(task_id)
                task_payload = task.model_dump(exclude_none=True)
                tasks.append(
                    {
                        "task": task_payload,
                        "payload": payload.model_dump(exclude_none=True) if payload is not None else None,
                    }
                )
            return {
                "schema_version": 1,
                "updated_at": self._now_datetime(),
                "tasks": tasks,
            }

    def _write_backtest_task_state_payload(self, payload: dict[str, object]) -> None:
        self._backtest_task_state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self._backtest_task_state_path.with_suffix(".tmp")
        tmp_path.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2),
            encoding="utf-8",
        )
        tmp_path.replace(self._backtest_task_state_path)

    def _persist_backtest_task_state(self, *, force: bool = False) -> None:
        now_ts = time.time()
        if (not force) and (now_ts - self._backtest_task_state_last_persist_at < 1.5):
            return
        try:
            payload = self._build_backtest_task_state_payload()
            self._write_backtest_task_state_payload(payload)
            self._backtest_task_state_last_persist_at = now_ts
        except Exception:
            pass

    def _load_backtest_task_state(self) -> None:
        if not self._backtest_task_state_path.exists():
            return
        try:
            raw = json.loads(self._backtest_task_state_path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                return
            items = raw.get("tasks")
            if not isinstance(items, list):
                return
            restored_tasks: dict[str, BacktestTaskStatusResponse] = {}
            restored_payloads: dict[str, BacktestRunRequest] = {}
            for row in items:
                if not isinstance(row, dict):
                    continue
                task_raw = row.get("task")
                if not isinstance(task_raw, dict):
                    continue
                try:
                    task = BacktestTaskStatusResponse(**task_raw)
                except Exception:
                    continue
                restored_tasks[task.task_id] = task
                payload_raw = row.get("payload")
                if isinstance(payload_raw, dict):
                    try:
                        restored_payloads[task.task_id] = BacktestRunRequest(**payload_raw)
                    except Exception:
                        pass
            with self._backtest_task_lock:
                self._backtest_tasks = restored_tasks
                self._backtest_task_payloads = restored_payloads
        except Exception:
            return

    def _should_auto_resume_backtest_tasks(self) -> bool:
        return self._env_flag("TDX_TREND_BACKTEST_TASK_AUTO_RESUME", False)

    def _resume_backtest_tasks_after_boot(self) -> None:
        resumable: list[tuple[str, BacktestRunRequest]] = []
        unrecoverable_task_ids: list[str] = []
        auto_resume = self._should_auto_resume_backtest_tasks()
        with self._backtest_task_lock:
            for task_id, task in list(self._backtest_tasks.items()):
                if task.status not in {"pending", "running"}:
                    continue
                payload = self._backtest_task_payloads.get(task_id)
                if payload is None:
                    unrecoverable_task_ids.append(task_id)
                    continue
                resumable.append((task_id, payload))

        now_text = self._now_datetime()
        for task_id in unrecoverable_task_ids:
            task = self.get_backtest_task(task_id)
            if task is None:
                continue
            failed_progress = task.progress.model_copy(
                update={
                    "message": "服务重启后无法恢复：缺少任务参数。",
                    "updated_at": now_text,
                }
            )
            self._upsert_backtest_task(
                task.model_copy(
                    update={
                        "status": "failed",
                        "progress": failed_progress,
                        "error": "服务重启后无法恢复任务：缺少任务参数。",
                        "error_code": "BACKTEST_TASK_RESUME_PAYLOAD_MISSING",
                    }
                )
            )

        for task_id, payload in resumable:
            task = self.get_backtest_task(task_id)
            if task is None:
                continue
            if not auto_resume:
                paused_progress = task.progress.model_copy(
                    update={
                        "message": "检测到服务重启，任务已自动暂停，请手动继续。",
                        "updated_at": now_text,
                    }
                )
                self._upsert_backtest_task(
                    task.model_copy(
                        update={
                            "status": "paused",
                            "progress": paused_progress,
                            "error": None,
                            "error_code": None,
                        }
                    )
                )
                continue
            pending_progress = task.progress.model_copy(
                update={
                    "message": "检测到服务重启，任务已自动续跑。",
                    "updated_at": now_text,
                }
            )
            self._upsert_backtest_task(
                task.model_copy(
                    update={
                        "status": "pending",
                        "progress": pending_progress,
                        "error": None,
                        "error_code": None,
                    }
                )
            )
            self._start_backtest_task_worker(task_id, payload, resumed=True)

    def _upsert_backtest_task(self, task: BacktestTaskStatusResponse) -> None:
        force_persist = task.status in {"paused", "succeeded", "failed", "cancelled"}
        with self._backtest_task_lock:
            self._backtest_tasks[task.task_id] = task
            if len(self._backtest_tasks) > 80:
                sorted_items = sorted(
                    self._backtest_tasks.items(),
                    key=lambda item: item[1].progress.updated_at,
                )
                for old_task_id, _ in sorted_items[: max(0, len(sorted_items) - 80)]:
                    self._backtest_tasks.pop(old_task_id, None)
                    self._backtest_task_payloads.pop(old_task_id, None)
        self._persist_backtest_task_state(force=force_persist)

    def get_backtest_task(self, task_id: str) -> BacktestTaskStatusResponse | None:
        with self._backtest_task_lock:
            task = self._backtest_tasks.get(task_id)
            if task is None:
                return None
            return task.model_copy(deep=True)

    def list_backtest_tasks(self, *, include_result: bool = False) -> BacktestTaskListResponse:
        with self._backtest_task_lock:
            tasks = sorted(
                self._backtest_tasks.values(),
                key=lambda row: row.progress.updated_at,
                reverse=True,
            )
            items: list[BacktestTaskStatusResponse] = []
            for row in tasks:
                copied = row.model_copy(deep=True)
                if not include_result:
                    copied = copied.model_copy(update={"result": None})
                items.append(copied)
        return BacktestTaskListResponse(items=items)

    def delete_backtest_task(self, task_id: str) -> BacktestTaskDeleteResponse:
        normalized_task_id = self._validate_backtest_task_id(task_id)
        with self._backtest_task_lock:
            task = self._backtest_tasks.get(normalized_task_id)
            if task is None:
                raise BacktestValidationError("BACKTEST_TASK_NOT_FOUND", "回测任务不存在")
            if task.status in {"pending", "running"}:
                raise BacktestValidationError(
                    "BACKTEST_TASK_CONTROL_INVALID",
                    f"任务当前状态为 {task.status}，请先暂停或停止后再删除。",
                )
            self._backtest_tasks.pop(normalized_task_id, None)
            self._backtest_task_payloads.pop(normalized_task_id, None)
            self._backtest_running_worker_ids.discard(normalized_task_id)
        self._persist_backtest_task_state(force=True)
        return BacktestTaskDeleteResponse(deleted=True, task_id=normalized_task_id)

    def get_backtest_strategy_signals(self, symbol: str) -> BacktestStrategySignalsResponse:
        symbol_lower = symbol.strip().lower()
        dedup: dict[tuple[str, str], BacktestStrategySignalPoint] = {}
        strategy_names: dict[str, str] = {}

        def _resolve_strategy_name(strategy_id: str) -> str:
            if strategy_id in strategy_names:
                return strategy_names[strategy_id]
            descriptor = self._strategy_registry.get(strategy_id)
            name = descriptor.name if descriptor else strategy_id
            strategy_names[strategy_id] = name
            return name

        with self._backtest_task_lock:
            tasks = list(self._backtest_tasks.values())

        for task in tasks:
            if task.status != "succeeded" or task.result is None:
                continue
            result = task.result
            plan_signals = result.plan_signals or []
            strategy_id = result.strategy_id or ""
            s_name = _resolve_strategy_name(strategy_id)
            for trade in plan_signals:
                if trade.symbol.lower() != symbol_lower:
                    continue
                key = (strategy_id, trade.signal_date)
                existing = dedup.get(key)
                if existing is None or task.progress.updated_at > existing.task_id:
                    dedup[key] = BacktestStrategySignalPoint(
                        signal_date=trade.signal_date,
                        entry_signal=trade.entry_signal,
                        entry_phase=trade.entry_phase,
                        event_grade=trade.event_grade or "C",
                        entry_quality_score=trade.entry_quality_score or 0.0,
                        strategy_id=strategy_id,
                        strategy_name=s_name,
                        task_id=task.task_id,
                        source_type="task",
                    )

        try:
            report_list = self.list_backtest_reports()
        except Exception:
            report_list = type("obj", (object,), {"items": []})()

        for summary in report_list.items:
            try:
                report = self.get_backtest_report(summary.report_id)
            except Exception:
                continue
            if report is None or report.run_result is None:
                continue
            result = report.run_result
            plan_signals = result.plan_signals or []
            strategy_id = result.strategy_id or ""
            s_name = _resolve_strategy_name(strategy_id)
            for trade in plan_signals:
                if trade.symbol.lower() != symbol_lower:
                    continue
                key = (strategy_id, trade.signal_date)
                existing = dedup.get(key)
                source_ts = summary.last_imported_at or ""
                if existing is None or source_ts > existing.task_id:
                    dedup[key] = BacktestStrategySignalPoint(
                        signal_date=trade.signal_date,
                        entry_signal=trade.entry_signal,
                        entry_phase=trade.entry_phase,
                        event_grade=trade.event_grade or "C",
                        entry_quality_score=trade.entry_quality_score or 0.0,
                        strategy_id=strategy_id,
                        strategy_name=s_name,
                        task_id=summary.report_id,
                        source_type="report",
                    )

        all_signals = list(dedup.values())
        strategy_stats: dict[str, dict[str, str | int]] = {}
        for sig in all_signals:
            sid = sig.strategy_id
            if sid not in strategy_stats:
                strategy_stats[sid] = {"count": 0, "date_from": sig.signal_date, "date_to": sig.signal_date}
            entry = strategy_stats[sid]
            entry["count"] = entry["count"] + 1
            if sig.signal_date < str(entry["date_from"]):
                entry["date_from"] = sig.signal_date
            if sig.signal_date > str(entry["date_to"]):
                entry["date_to"] = sig.signal_date

        strategies = [
            BacktestStrategySignalStrategyInfo(
                strategy_id=sid,
                strategy_name=_resolve_strategy_name(sid),
                signal_count=int(info["count"]),
                date_from=str(info["date_from"]),
                date_to=str(info["date_to"]),
            )
            for sid, info in strategy_stats.items()
        ]
        strategies.sort(key=lambda s: s.strategy_id)

        return BacktestStrategySignalsResponse(
            symbol=symbol.strip(),
            strategies=strategies,
            signals=all_signals,
        )

    def scan_stock_strategy_signals(
        self,
        symbol: str,
        window_days: int = 60,
        scan_days: int = 120,
    ) -> BacktestStrategySignalsResponse:
        symbol = symbol.strip()
        symbol_lower = symbol.lower()
        candles = self._ensure_candles(symbol_lower)
        if len(candles) < 30:
            return BacktestStrategySignalsResponse(symbol=symbol)

        active_strategies = [
            d for d in self._strategy_registry.list()
            if d.enabled and not d.strategy_id.startswith("__removed_")
        ]
        if not active_strategies:
            return BacktestStrategySignalsResponse(symbol=symbol)

        start_idx = max(0, len(candles) - scan_days)
        scan_candles = candles[start_idx:]
        signals: list[BacktestStrategySignalPoint] = []

        for offset in range(len(scan_candles)):
            as_of_date = scan_candles[offset].time
            sliced, _ = self._slice_candles_as_of(candles, as_of_date)
            if len(sliced) < 30:
                continue

            row = self._build_row_from_candles(symbol_lower, as_of_date)
            if row is None:
                continue

            snapshot = self._calc_wyckoff_snapshot(row, window_days, as_of_date=as_of_date)
            snapshot = self._attach_ths_volume_signal(snapshot, sliced, symbol_lower)

            for desc in active_strategies:
                sid = desc.strategy_id
                params = dict(desc.default_params)
                try:
                    triggered = self._strategy_registry.generate_signals(
                        strategy_id=sid,
                        row=row,
                        snapshot=snapshot,
                        params=params,
                    )
                except Exception:
                    triggered = False
                if not triggered:
                    continue

                entry_signal = ""
                entry_phase = ""
                event_grade = "C"
                entry_quality_score = 0.0

                ths_ind = snapshot.get("ths_main_retail_signal")
                if isinstance(ths_ind, dict):
                    if ths_ind.get("golden_cross"):
                        entry_signal = "金叉"
                    elif ths_ind.get("purple_to_yellow"):
                        entry_signal = "紫转黄"

                rhythm_ind = snapshot.get("force_rhythm_signal")
                if isinstance(rhythm_ind, dict) and sid == "ths_force_rhythm_v1":
                    evaluation = evaluate_force_rhythm_signal(rhythm_ind, params)
                    if evaluation.get("signal"):
                        entry_signal = "节奏波谷"

                wk_ind = snapshot.get("wulong_cluster_signal")
                if isinstance(wk_ind, dict) and sid.startswith("wulong"):
                    if wk_ind.get("signal"):
                        entry_signal = "五龙聚首"

                b1_ind = snapshot.get("b1_mtf_signal")
                if isinstance(b1_ind, dict) and sid.startswith("b1"):
                    if b1_ind.get("signal"):
                        entry_signal = "B1信号"

                tk_ind = snapshot.get("trend_king_signal")
                if isinstance(tk_ind, dict) and sid.startswith("trend_king"):
                    if tk_ind.get("signal"):
                        entry_signal = tk_ind.get("label") or "趋势为王"

                elu_ind = snapshot.get("emotion_limit_up_signal")
                if isinstance(elu_ind, dict) and sid == "emotion_limit_up_v1":
                    evaluation = evaluate_emotion_limit_up_signal(elu_ind, params)
                    if evaluation.get("signal"):
                        entry_signal = str(evaluation.get("label") or "情绪涨停")

                lua_ind = snapshot.get("limit_up_arb_signal")
                if isinstance(lua_ind, dict) and sid == "limit_up_arb_v1":
                    evaluation = evaluate_limit_up_arb_signal(lua_ind, params)
                    if evaluation.get("signal"):
                        entry_signal = str(evaluation.get("label") or "涨停套利")

                if sid.startswith("matrix_signal"):
                    entry_signal = "矩阵信号"
                elif sid.startswith("relative_strength"):
                    entry_signal = "相对强弱突破"
                elif sid.startswith("score_only"):
                    entry_signal = "ScoreOnly"

                wyckoff_phase = snapshot.get("wyckoff_phase", "")
                if isinstance(wyckoff_phase, str) and wyckoff_phase:
                    entry_phase = wyckoff_phase

                score_val = snapshot.get("entry_quality_score")
                if isinstance(score_val, (int, float)):
                    entry_quality_score = float(score_val)

                grade_val = snapshot.get("event_grade")
                if isinstance(grade_val, str) and grade_val in ("A", "B"):
                    event_grade = grade_val

                if not entry_signal:
                    wy_signal = snapshot.get("wyckoff_signal")
                    if isinstance(wy_signal, str) and wy_signal:
                        entry_signal = wy_signal
                    else:
                        entry_signal = "信号触发"

                signals.append(BacktestStrategySignalPoint(
                    signal_date=as_of_date,
                    entry_signal=entry_signal,
                    entry_phase=entry_phase,
                    event_grade=event_grade,
                    entry_quality_score=round(entry_quality_score, 2),
                    strategy_id=sid,
                    strategy_name=desc.name,
                    task_id="",
                    source_type="scan",
                ))

        # Compute returns per signal
        close_by_date: dict[str, float] = {}
        for c in candles:
            close_by_date[c.time] = c.close
        sorted_dates = sorted(close_by_date.keys())
        date_index = {d: i for i, d in enumerate(sorted_dates)}

        for sig in signals:
            if sig.signal_date not in date_index:
                continue
            di = date_index[sig.signal_date]
            entry_idx = di + 1
            if entry_idx >= len(sorted_dates):
                continue
            entry_price = close_by_date[sorted_dates[entry_idx]]
            for hold, attr in [(1, "return_1d"), (3, "return_3d"), (5, "return_5d")]:
                exit_idx = entry_idx + hold
                if exit_idx < len(sorted_dates):
                    exit_price = close_by_date[sorted_dates[exit_idx]]
                    ret = (exit_price - entry_price) / max(entry_price, 0.01)
                    setattr(sig, attr, round(ret, 4))

        strategy_stats: dict[str, dict[str, Any]] = {}
        for sig in signals:
            sid = sig.strategy_id
            if sid not in strategy_stats:
                strategy_stats[sid] = {
                    "count": 0, "date_from": sig.signal_date, "date_to": sig.signal_date,
                    "returns_1d": [], "returns_3d": [], "returns_5d": [],
                }
            entry = strategy_stats[sid]
            entry["count"] = entry["count"] + 1
            if sig.signal_date < str(entry["date_from"]):
                entry["date_from"] = sig.signal_date
            if sig.signal_date > str(entry["date_to"]):
                entry["date_to"] = sig.signal_date
            if sig.return_1d != 0.0:
                entry["returns_1d"].append(sig.return_1d)
            if sig.return_3d != 0.0:
                entry["returns_3d"].append(sig.return_3d)
            if sig.return_5d != 0.0:
                entry["returns_5d"].append(sig.return_5d)

        strategies = []
        for sid, info in strategy_stats.items():
            def _win_avg(returns: list[float]) -> tuple[float, float]:
                if not returns:
                    return 0.0, 0.0
                wins = sum(1 for r in returns if r > 0)
                return round(wins / len(returns), 4), round(sum(returns) / len(returns), 4)

            wr1, ar1 = _win_avg(info["returns_1d"])
            wr3, ar3 = _win_avg(info["returns_3d"])
            wr5, ar5 = _win_avg(info["returns_5d"])
            strategies.append(BacktestStrategySignalStrategyInfo(
                strategy_id=sid,
                strategy_name=next((d.name for d in active_strategies if d.strategy_id == sid), sid),
                signal_count=int(info["count"]),
                date_from=str(info["date_from"]),
                date_to=str(info["date_to"]),
                win_rate_1d=wr1,
                avg_return_1d=ar1,
                win_rate_3d=wr3,
                avg_return_3d=ar3,
                win_rate_5d=wr5,
                avg_return_5d=ar5,
            ))
        strategies.sort(key=lambda s: (-s.avg_return_5d, -s.win_rate_5d))

        return BacktestStrategySignalsResponse(
            symbol=symbol,
            strategies=strategies,
            signals=signals,
        )

    def _await_backtest_task_runnable(self, task_id: str) -> None:
        while True:
            task = self.get_backtest_task(task_id)
            if task is None:
                raise BacktestTaskCancelledError("任务不存在，无法继续执行。")
            if task.status == "cancelled":
                raise BacktestTaskCancelledError("任务已停止。")
            if task.status in {"succeeded", "failed"}:
                raise BacktestTaskCancelledError(f"任务状态已结束：{task.status}")
            if task.status == "paused":
                time.sleep(0.25)
                continue
            if task.status in {"pending", "running"}:
                return
            time.sleep(0.25)

    def _control_backtest_task(
        self,
        task_id: str,
        action: Literal["pause", "resume", "cancel"],
    ) -> BacktestTaskStatusResponse:
        payload_for_resume: BacktestRunRequest | None = None
        now_text = self._now_datetime()
        with self._backtest_task_lock:
            task = self._backtest_tasks.get(task_id)
            if task is None:
                raise BacktestValidationError("BACKTEST_TASK_NOT_FOUND", "回测任务不存在")
            if action == "pause":
                if task.status in {"succeeded", "failed", "cancelled"}:
                    raise BacktestValidationError(
                        "BACKTEST_TASK_CONTROL_INVALID",
                        f"任务当前状态为 {task.status}，无法暂停。",
                    )
                if task.status != "paused":
                    task = task.model_copy(
                        update={
                            "status": "paused",
                            "progress": task.progress.model_copy(
                                update={
                                    "message": "任务已暂停。",
                                    "updated_at": now_text,
                                }
                            ),
                            "error": None,
                            "error_code": None,
                        }
                    )
                    self._backtest_tasks[task_id] = task
            elif action == "resume":
                if task.status in {"succeeded", "failed", "cancelled"}:
                    raise BacktestValidationError(
                        "BACKTEST_TASK_CONTROL_INVALID",
                        f"任务当前状态为 {task.status}，无法继续执行。",
                    )
                if task.status == "paused":
                    payload_for_resume = self._backtest_task_payloads.get(task_id)
                    if payload_for_resume is None:
                        raise BacktestValidationError(
                            "BACKTEST_TASK_RESUME_PAYLOAD_MISSING",
                            "任务参数缺失，无法继续执行。",
                        )
                    task = task.model_copy(
                        update={
                            "status": "pending",
                            "progress": task.progress.model_copy(
                                update={
                                    "message": "任务已恢复，等待继续执行。",
                                    "updated_at": now_text,
                                }
                            ),
                            "error": None,
                            "error_code": None,
                        }
                    )
                    self._backtest_tasks[task_id] = task
            else:
                if task.status not in {"succeeded", "failed", "cancelled"}:
                    task = task.model_copy(
                        update={
                            "status": "cancelled",
                            "progress": task.progress.model_copy(
                                update={
                                    "message": "任务已停止。",
                                    "updated_at": now_text,
                                }
                            ),
                            "error": None,
                            "error_code": None,
                        }
                    )
                    self._backtest_tasks[task_id] = task

        current = self.get_backtest_task(task_id)
        if current is None:
            raise BacktestValidationError("BACKTEST_TASK_NOT_FOUND", "回测任务不存在")
        self._persist_backtest_task_state(force=current.status in {"paused", "cancelled"})
        if action == "resume" and payload_for_resume is not None:
            self._start_backtest_task_worker(task_id, payload_for_resume, resumed=True)
        return current

    def pause_backtest_task(self, task_id: str) -> BacktestTaskStatusResponse:
        return self._control_backtest_task(task_id, "pause")

    def resume_backtest_task(self, task_id: str) -> BacktestTaskStatusResponse:
        return self._control_backtest_task(task_id, "resume")

    def cancel_backtest_task(self, task_id: str) -> BacktestTaskStatusResponse:
        return self._control_backtest_task(task_id, "cancel")

    def _start_backtest_task_worker(
        self,
        task_id: str,
        payload: BacktestRunRequest,
        *,
        resumed: bool = False,
        run_precheck: bool = False,
    ) -> None:
        with self._backtest_task_lock:
            if task_id in self._backtest_running_worker_ids:
                return
            self._backtest_running_worker_ids.add(task_id)

        def _worker() -> None:
            try:
                task = self.get_backtest_task(task_id)
                if task is None:
                    return
                task_stage_timings: list[BacktestTaskStageTiming] = list(task.progress.stage_timings)

                def _upsert_stage_timing(stage_timing: BacktestTaskStageTiming) -> None:
                    nonlocal task_stage_timings
                    stage_key = str(stage_timing.stage_key or "").strip()
                    if not stage_key:
                        return
                    updated = False
                    next_rows: list[BacktestTaskStageTiming] = []
                    for row in task_stage_timings:
                        if str(row.stage_key) == stage_key:
                            next_rows.append(stage_timing)
                            updated = True
                        else:
                            next_rows.append(row)
                    if not updated:
                        next_rows.append(stage_timing)
                    task_stage_timings = next_rows
                    current_task = self.get_backtest_task(task_id)
                    if current_task is None:
                        return
                    next_progress = current_task.progress.model_copy(
                        update={
                            "stage_timings": list(task_stage_timings),
                            "updated_at": self._now_datetime(),
                        }
                    )
                    self._upsert_backtest_task(current_task.model_copy(update={"progress": next_progress}))

                self._set_backtest_runtime_stage_timing_callback(
                    lambda stage_key, label, elapsed_sec: _upsert_stage_timing(
                        self._build_backtest_task_stage_timing(stage_key, label, elapsed_sec)
                    )
                )

                self._await_backtest_task_runnable(task_id)
                task = self.get_backtest_task(task_id)
                if task is None:
                    return
                running_message = "任务执行中..." if not resumed else "服务重启后自动续跑中..."
                running_progress = task.progress.model_copy(
                    update={
                        "message": running_message,
                        "stage_timings": list(task_stage_timings),
                        "updated_at": self._now_datetime(),
                    }
                )
                self._upsert_backtest_task(
                    task.model_copy(
                        update={
                            "status": "running",
                            "progress": running_progress,
                            "error": None,
                            "error_code": None,
                        }
                    )
                )

                if run_precheck:
                    self._await_backtest_task_runnable(task_id)
                    precheck_task = self.get_backtest_task(task_id)
                    if precheck_task is not None:
                        precheck_progress = precheck_task.progress.model_copy(
                            update={
                                "message": "任务预检中：检查K线覆盖...",
                                "updated_at": self._now_datetime(),
                            }
                        )
                        self._upsert_backtest_task(
                            precheck_task.model_copy(
                                update={
                                    "status": "running",
                                    "progress": precheck_progress,
                                }
                            )
                        )
                    precheck_start_ts = time.perf_counter()
                    self._run_backtest_precheck_with_cache(payload)
                    precheck_elapsed = time.perf_counter() - precheck_start_ts
                    _upsert_stage_timing(
                        self._build_backtest_task_stage_timing(
                            "precheck",
                            "前置校验",
                            precheck_elapsed,
                        )
                    )
                    post_precheck_task = self.get_backtest_task(task_id)
                    if post_precheck_task is not None:
                        post_precheck_progress = post_precheck_task.progress.model_copy(
                            update={
                                "message": "任务预检完成，开始回测...",
                                "stage_timings": list(task_stage_timings),
                                "updated_at": self._now_datetime(),
                            }
                        )
                        self._upsert_backtest_task(
                            post_precheck_task.model_copy(
                                update={
                                    "status": "running",
                                    "progress": post_precheck_progress,
                                }
                            )
                        )

                def _progress(current_date: str, processed_dates: int, total: int, message: str) -> None:
                    self._await_backtest_task_runnable(task_id)
                    current_task = self.get_backtest_task(task_id)
                    if current_task is None:
                        return
                    total_safe = max(1, int(total), int(current_task.progress.total_dates or 0))
                    processed_safe = max(0, min(int(processed_dates), total_safe))
                    processed_safe = max(int(current_task.progress.processed_dates or 0), processed_safe)
                    percent = round((processed_safe / total_safe) * 100.0, 2)
                    next_progress = current_task.progress.model_copy(
                        update={
                            "current_date": current_date,
                            "processed_dates": processed_safe,
                            "total_dates": total_safe,
                            "percent": percent,
                            "message": message,
                            "stage_timings": list(task_stage_timings),
                            "updated_at": self._now_datetime(),
                        }
                    )
                    self._upsert_backtest_task(
                        current_task.model_copy(update={"status": "running", "progress": next_progress})
                    )

                run_start_ts = time.perf_counter()
                result = self.run_backtest(
                    payload,
                    progress_callback=_progress,
                    control_callback=lambda: self._await_backtest_task_runnable(task_id),
                )
                run_elapsed = time.perf_counter() - run_start_ts
                for stage_timing in self._extract_backtest_stage_timings(result, run_elapsed_sec=run_elapsed):
                    _upsert_stage_timing(stage_timing)
                finished_task = self.get_backtest_task(task_id)
                if finished_task is None:
                    return
                done_progress = finished_task.progress.model_copy(
                    update={
                        "percent": 100.0,
                        "processed_dates": max(finished_task.progress.processed_dates, finished_task.progress.total_dates),
                        "message": "回测完成。",
                        "stage_timings": list(task_stage_timings),
                        "updated_at": self._now_datetime(),
                    }
                )
                self._upsert_backtest_task(
                    finished_task.model_copy(
                        update={
                            "status": "succeeded",
                            "progress": done_progress,
                            "result": result,
                            "error": None,
                            "error_code": None,
                        }
                    )
                )
            except BacktestTaskCancelledError:
                cancelled_task = self.get_backtest_task(task_id)
                if cancelled_task is None:
                    return
                if cancelled_task.status == "cancelled":
                    return
                cancelled_progress = cancelled_task.progress.model_copy(
                    update={
                        "message": "任务已停止。",
                        "stage_timings": list(task_stage_timings),
                        "updated_at": self._now_datetime(),
                    }
                )
                self._upsert_backtest_task(
                    cancelled_task.model_copy(
                        update={
                            "status": "cancelled",
                            "progress": cancelled_progress,
                            "error": None,
                            "error_code": None,
                        }
                    )
                )
            except Exception as exc:  # noqa: BLE001
                failed_task = self.get_backtest_task(task_id)
                if failed_task is None:
                    return
                failed_progress = failed_task.progress.model_copy(
                    update={
                        "message": "回测失败。",
                        "stage_timings": list(task_stage_timings),
                        "updated_at": self._now_datetime(),
                    }
                )
                error_code = "BACKTEST_TASK_FAILED"
                if isinstance(exc, BacktestValidationError):
                    error_code = exc.code
                self._upsert_backtest_task(
                    failed_task.model_copy(
                        update={
                            "status": "failed",
                            "progress": failed_progress,
                            "error": str(exc),
                            "error_code": error_code,
                        }
                    )
                )
            finally:
                self._clear_backtest_runtime_stage_timing_callback()
                with self._backtest_task_lock:
                    self._backtest_running_worker_ids.discard(task_id)
                self.maybe_trim_backtest_runtime_memory()
                self._persist_backtest_task_state(force=True)

        try:
            Thread(target=keep_awake_while(_worker), daemon=True).start()
        except Exception as exc:
            with self._backtest_task_lock:
                self._backtest_running_worker_ids.discard(task_id)
            failed_task = self.get_backtest_task(task_id)
            if failed_task is not None and failed_task.status not in {"succeeded", "failed", "cancelled"}:
                failed_progress = failed_task.progress.model_copy(
                    update={
                        "message": "回测任务启动失败。",
                        "updated_at": self._now_datetime(),
                    }
                )
                self._upsert_backtest_task(
                    failed_task.model_copy(
                        update={
                            "status": "failed",
                            "progress": failed_progress,
                            "error": f"任务线程启动失败：{exc}",
                            "error_code": "BACKTEST_TASK_WORKER_START_FAILED",
                        }
                    )
                )
            self._persist_backtest_task_state(force=True)

    def start_backtest_task(self, payload: BacktestRunRequest) -> str:
        payload, _profile, _profile_id, _profile_hash = self._bind_backtest_event_judgment_profile(payload)
        async_precheck = self._is_backtest_task_precheck_async_enabled()
        sync_precheck_stage: BacktestTaskStageTiming | None = None
        if not async_precheck:
            precheck_start_ts = time.perf_counter()
            self._run_backtest_precheck_with_cache(payload)
            sync_precheck_stage = self._build_backtest_task_stage_timing(
                "precheck",
                "前置校验",
                time.perf_counter() - precheck_start_ts,
            )

        task_id = f"bt_{uuid4().hex[:16]}"
        now_text = self._now_datetime()
        scan_total_dates = self._estimate_backtest_scan_progress_total_dates(payload)
        total_dates = self._estimate_backtest_progress_total_dates(payload)
        warning: str | None = None
        if payload.pool_roll_mode in {"daily", "weekly"} and scan_total_dates >= 45:
            warning = "滚动回测日期较长，耗时可能较久，请耐心等待。"

        initial_progress = BacktestTaskProgress(
            mode=payload.pool_roll_mode,
            current_date=None,
            processed_dates=0,
            total_dates=total_dates,
            percent=0.0,
            message=("任务已创建，等待预检。" if async_precheck else "任务已创建，等待执行。"),
            warning=warning,
            stage_timings=([sync_precheck_stage] if sync_precheck_stage is not None else []),
            started_at=now_text,
            updated_at=now_text,
        )
        with self._backtest_task_lock:
            self._backtest_task_payloads[task_id] = payload.model_copy(deep=True)
        self._upsert_backtest_task(
            BacktestTaskStatusResponse(
                task_id=task_id,
                status="pending",
                progress=initial_progress,
                result=None,
                error=None,
                error_code=None,
            )
        )
        self._start_backtest_task_worker(
            task_id,
            payload,
            resumed=False,
            run_precheck=async_precheck,
        )
        return task_id

    @classmethod
    def _validate_backtest_task_id(cls, task_id: str, *, field_name: str = "task_id") -> str:
        text = str(task_id or "").strip()
        if not cls._BACKTEST_TASK_ID_RE.fullmatch(text):
            raise BacktestValidationError("BACKTEST_TASK_INVALID_ID", f"{field_name} 不合法。")
        return text

    @classmethod
    def _validate_backtest_plateau_detail_key(cls, detail_key: str) -> str:
        text = str(detail_key or "").strip()
        if not cls._BACKTEST_PLATEAU_POINT_DETAIL_KEY_RE.fullmatch(text):
            raise BacktestValidationError("BACKTEST_PLATEAU_POINT_DETAIL_INVALID", "收益平原参数组明细ID不合法。")
        return text

    @staticmethod
    def _build_backtest_plateau_detail_key(params: BacktestPlateauParams) -> str:
        payload = json.dumps(
            params.model_dump(exclude_none=True),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return f"pt_{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:16]}"

    def _backtest_plateau_task_detail_dir(self, task_id: str) -> Path:
        normalized_task_id = self._validate_backtest_task_id(task_id)
        return self._resolve_backtest_plateau_detail_store_dir() / normalized_task_id

    def _backtest_plateau_point_detail_path(self, task_id: str, detail_key: str) -> Path:
        normalized_detail_key = self._validate_backtest_plateau_detail_key(detail_key)
        return self._backtest_plateau_task_detail_dir(task_id) / f"{normalized_detail_key}.json"

    def _persist_backtest_plateau_point_detail(
        self,
        *,
        task_id: str,
        detail_key: str,
        params: BacktestPlateauParams,
        run_request: BacktestRunRequest,
        run_result: BacktestResponse,
    ) -> None:
        path = self._backtest_plateau_point_detail_path(task_id, detail_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        body = {
            "schema_version": 1,
            "task_id": self._validate_backtest_task_id(task_id),
            "detail_key": self._validate_backtest_plateau_detail_key(detail_key),
            "saved_at": self._now_datetime(),
            "params": params.model_dump(exclude_none=True),
            "run_request": run_request.model_dump(exclude_none=True),
            "run_result": run_result.model_dump(exclude_none=True),
        }
        tmp_path = path.with_suffix(".tmp")
        tmp_path.write_text(
            json.dumps(body, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        tmp_path.replace(path)

    def _clear_backtest_plateau_task_detail_store(self, task_id: str) -> None:
        try:
            target_dir = self._backtest_plateau_task_detail_dir(task_id)
        except BacktestValidationError:
            return
        if not target_dir.exists():
            return
        shutil.rmtree(target_dir, ignore_errors=True)

    def get_backtest_plateau_point_detail(
        self,
        task_id: str,
        detail_key: str,
    ) -> BacktestPlateauPointDetailResponse | None:
        try:
            path = self._backtest_plateau_point_detail_path(task_id, detail_key)
        except BacktestValidationError:
            return None
        if not path.exists():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                return None
            return BacktestPlateauPointDetailResponse(
                task_id=self._validate_backtest_task_id(str(raw.get("task_id") or task_id)),
                detail_key=self._validate_backtest_plateau_detail_key(str(raw.get("detail_key") or detail_key)),
                saved_at=str(raw.get("saved_at") or ""),
                params=BacktestPlateauParams(**dict(raw.get("params") or {})),
                run_request=BacktestRunRequest(**dict(raw.get("run_request") or {})),
                run_result=BacktestResponse(**dict(raw.get("run_result") or {})),
            )
        except Exception:
            return None

    def _estimate_backtest_plateau_total_points(self, payload: BacktestPlateauRunRequest) -> int:
        sample_points = max(1, int(self._resolve_plateau_sample_points(payload)))
        if payload.sampling_mode == "lhs":
            return sample_points
        base = payload.base_payload
        axis_sizes = [
            len(self._normalize_plateau_axis_int(payload.window_days_list, base=int(base.window_days), lower=20, upper=240)),
            len(self._normalize_plateau_axis_float(payload.min_score_list, base=float(base.min_score), lower=0.0, upper=100.0, precision=4)),
            len(self._normalize_plateau_axis_float(payload.stop_loss_list, base=float(base.stop_loss), lower=0.0, upper=0.5, precision=6)),
            len(self._normalize_plateau_axis_float(payload.take_profit_list, base=float(base.take_profit), lower=0.0, upper=1.5, precision=6)),
            len(self._normalize_plateau_axis_float(payload.trailing_stop_pct_list, base=float(base.trailing_stop_pct), lower=0.0, upper=0.5, precision=6)),
            len(self._normalize_plateau_axis_float(
                payload.intraday_trailing_reduce_ratio_list,
                base=float(base.intraday_trailing_reduce_ratio),
                lower=0.1,
                upper=1.0,
                precision=6,
            )),
            len(self._normalize_plateau_axis_int(payload.max_positions_list, base=int(base.max_positions), lower=1, upper=100)),
            len(self._normalize_plateau_axis_float(payload.position_pct_list, base=float(base.position_pct), lower=0.0001, upper=1.0, precision=6)),
            len(self._normalize_plateau_axis_int(payload.max_symbols_list, base=int(base.max_symbols), lower=20, upper=2000)),
            len(self._normalize_plateau_axis_int(payload.priority_topk_per_day_list, base=int(base.priority_topk_per_day), lower=0, upper=500)),
        ]
        grid_total = 1
        for size in axis_sizes:
            grid_total *= max(1, int(size))
        if grid_total > 2000:
            raise ValueError(f"完整网格共 {grid_total} 点，超过单次2000点；请缩小参数范围或使用区间采样")
        return max(1, int(grid_total))

    def _build_backtest_plateau_task_state_payload(self) -> dict[str, object]:
        with self._backtest_plateau_task_lock:
            tasks = []
            for task_id, task in self._backtest_plateau_tasks.items():
                payload = self._backtest_plateau_task_payloads.get(task_id)
                task_payload = task.model_dump(exclude_none=True)
                tasks.append(
                    {
                        "task": task_payload,
                        "payload": payload.model_dump(exclude_none=True) if payload is not None else None,
                    }
                )
            return {
                "schema_version": 1,
                "updated_at": self._now_datetime(),
                "tasks": tasks,
            }

    def _write_backtest_plateau_task_state_payload(self, payload: dict[str, object]) -> None:
        self._backtest_plateau_task_state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self._backtest_plateau_task_state_path.with_suffix(".tmp")
        tmp_path.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2),
            encoding="utf-8",
        )
        tmp_path.replace(self._backtest_plateau_task_state_path)

    def _persist_backtest_plateau_task_state(self, *, force: bool = False) -> None:
        now_ts = time.time()
        if (not force) and (now_ts - self._backtest_plateau_task_state_last_persist_at < 1.5):
            return
        try:
            payload = self._build_backtest_plateau_task_state_payload()
            self._write_backtest_plateau_task_state_payload(payload)
            self._backtest_plateau_task_state_last_persist_at = now_ts
        except Exception:
            pass

    def _load_backtest_plateau_task_state(self) -> None:
        if not self._backtest_plateau_task_state_path.exists():
            return
        try:
            raw = json.loads(self._backtest_plateau_task_state_path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                return
            items = raw.get("tasks")
            if not isinstance(items, list):
                return
            restored_tasks: dict[str, BacktestPlateauTaskStatusResponse] = {}
            restored_payloads: dict[str, BacktestPlateauRunRequest] = {}
            for row in items:
                if not isinstance(row, dict):
                    continue
                task_raw = row.get("task")
                if not isinstance(task_raw, dict):
                    continue
                try:
                    task = BacktestPlateauTaskStatusResponse(**task_raw)
                except Exception:
                    continue
                if task.result is not None:
                    try:
                        task = task.model_copy(
                            update={
                                "result": self._rehydrate_backtest_plateau_result(task.result),
                            },
                            deep=True,
                        )
                    except Exception:
                        pass
                restored_tasks[task.task_id] = task
                payload_raw = row.get("payload")
                if isinstance(payload_raw, dict):
                    try:
                        restored_payloads[task.task_id] = BacktestPlateauRunRequest(**payload_raw)
                    except Exception:
                        pass
            with self._backtest_plateau_task_lock:
                self._backtest_plateau_tasks = restored_tasks
                self._backtest_plateau_task_payloads = restored_payloads
        except Exception:
            return

    def _should_auto_resume_backtest_plateau_tasks(self) -> bool:
        return self._env_flag("TDX_TREND_BACKTEST_PLATEAU_TASK_AUTO_RESUME", False)

    def _resume_backtest_plateau_tasks_after_boot(self) -> None:
        resumable: list[tuple[str, BacktestPlateauRunRequest]] = []
        unrecoverable_task_ids: list[str] = []
        auto_resume = self._should_auto_resume_backtest_plateau_tasks()
        with self._backtest_plateau_task_lock:
            for task_id, task in list(self._backtest_plateau_tasks.items()):
                if task.status not in {"pending", "running"}:
                    continue
                payload = self._backtest_plateau_task_payloads.get(task_id)
                if payload is None:
                    unrecoverable_task_ids.append(task_id)
                    continue
                resumable.append((task_id, payload))

        now_text = self._now_datetime()
        for task_id in unrecoverable_task_ids:
            task = self.get_backtest_plateau_task(task_id)
            if task is None:
                continue
            failed_progress = task.progress.model_copy(
                update={
                    "message": "服务重启后无法恢复：缺少任务参数。",
                    "updated_at": now_text,
                }
            )
            self._upsert_backtest_plateau_task(
                task.model_copy(
                    update={
                        "status": "failed",
                        "progress": failed_progress,
                        "error": "服务重启后无法恢复任务：缺少任务参数。",
                        "error_code": "BACKTEST_PLATEAU_TASK_RESUME_PAYLOAD_MISSING",
                    }
                )
            )

        for task_id, payload in resumable:
            task = self.get_backtest_plateau_task(task_id)
            if task is None:
                continue
            if not auto_resume:
                paused_progress = task.progress.model_copy(
                    update={
                        "message": "检测到服务重启，任务已自动暂停，请手动继续。",
                        "updated_at": now_text,
                    }
                )
                self._upsert_backtest_plateau_task(
                    task.model_copy(
                        update={
                            "status": "paused",
                            "progress": paused_progress,
                            "error": None,
                            "error_code": None,
                        }
                    )
                )
                continue
            pending_progress = task.progress.model_copy(
                update={
                    "message": "检测到服务重启，任务已自动续跑。",
                    "updated_at": now_text,
                }
            )
            self._upsert_backtest_plateau_task(
                task.model_copy(
                    update={
                        "status": "pending",
                        "progress": pending_progress,
                        "error": None,
                        "error_code": None,
                    }
                )
            )
            self._start_backtest_plateau_task_worker(task_id, payload, resumed=True)

    def _upsert_backtest_plateau_task(self, task: BacktestPlateauTaskStatusResponse) -> None:
        force_persist = task.status in {"paused", "succeeded", "failed", "cancelled"}
        removed_task_ids: list[str] = []
        with self._backtest_plateau_task_lock:
            self._backtest_plateau_tasks[task.task_id] = task
            if len(self._backtest_plateau_tasks) > 80:
                sorted_items = sorted(
                    self._backtest_plateau_tasks.items(),
                    key=lambda item: item[1].progress.updated_at,
                )
                for old_task_id, _ in sorted_items[: max(0, len(sorted_items) - 80)]:
                    self._backtest_plateau_tasks.pop(old_task_id, None)
                    self._backtest_plateau_task_payloads.pop(old_task_id, None)
                    removed_task_ids.append(old_task_id)
        for old_task_id in removed_task_ids:
            self._clear_backtest_plateau_task_detail_store(old_task_id)
        self._persist_backtest_plateau_task_state(force=force_persist)

    def get_backtest_plateau_task(self, task_id: str) -> BacktestPlateauTaskStatusResponse | None:
        with self._backtest_plateau_task_lock:
            task = self._backtest_plateau_tasks.get(task_id)
            if task is None:
                return None
            copied = task.model_copy(deep=True)
        if copied.result is not None:
            try:
                copied = copied.model_copy(
                    update={
                        "result": self._rehydrate_backtest_plateau_result(copied.result),
                    },
                    deep=True,
                )
            except Exception:
                pass
        return copied

    def list_backtest_plateau_tasks(self, *, include_result: bool = False) -> BacktestPlateauTaskListResponse:
        with self._backtest_plateau_task_lock:
            tasks = sorted(
                self._backtest_plateau_tasks.values(),
                key=lambda row: row.progress.updated_at,
                reverse=True,
            )
            items: list[BacktestPlateauTaskStatusResponse] = []
            for row in tasks:
                copied = row.model_copy(deep=True)
                if include_result and copied.result is not None:
                    try:
                        copied = copied.model_copy(
                            update={
                                "result": self._rehydrate_backtest_plateau_result(copied.result),
                            },
                            deep=True,
                        )
                    except Exception:
                        pass
                if not include_result:
                    copied = copied.model_copy(update={"result": None})
                items.append(copied)
        return BacktestPlateauTaskListResponse(items=items)

    def delete_backtest_plateau_task(self, task_id: str) -> BacktestPlateauTaskDeleteResponse:
        with self._backtest_plateau_task_lock:
            task = self._backtest_plateau_tasks.get(task_id)
            if task is None:
                raise BacktestValidationError("BACKTEST_PLATEAU_TASK_NOT_FOUND", "收益平原任务不存在")
            if task.status in {"pending", "running"}:
                raise BacktestValidationError(
                    "BACKTEST_PLATEAU_TASK_CONTROL_INVALID",
                    f"任务当前状态为 {task.status}，请先暂停或停止后再删除。",
                )
            self._backtest_plateau_tasks.pop(task_id, None)
            self._backtest_plateau_task_payloads.pop(task_id, None)
            self._backtest_plateau_running_worker_ids.discard(task_id)
        self._clear_backtest_plateau_task_detail_store(task_id)
        self._persist_backtest_plateau_task_state(force=True)
        return BacktestPlateauTaskDeleteResponse(deleted=True, task_id=task_id)

    def _await_backtest_plateau_task_runnable(self, task_id: str) -> None:
        while True:
            task = self.get_backtest_plateau_task(task_id)
            if task is None:
                raise BacktestTaskCancelledError("任务不存在，无法继续执行。")
            if task.status == "cancelled":
                raise BacktestTaskCancelledError("任务已停止。")
            if task.status in {"succeeded", "failed"}:
                raise BacktestTaskCancelledError(f"任务状态已结束：{task.status}")
            if task.status == "paused":
                time.sleep(0.25)
                continue
            if task.status in {"pending", "running"}:
                return
            time.sleep(0.25)

    def _control_backtest_plateau_task(
        self,
        task_id: str,
        action: Literal["pause", "resume", "cancel"],
    ) -> BacktestPlateauTaskStatusResponse:
        payload_for_resume: BacktestPlateauRunRequest | None = None
        now_text = self._now_datetime()
        with self._backtest_plateau_task_lock:
            task = self._backtest_plateau_tasks.get(task_id)
            if task is None:
                raise BacktestValidationError("BACKTEST_PLATEAU_TASK_NOT_FOUND", "收益平原任务不存在")
            if action == "pause":
                if task.status in {"succeeded", "failed", "cancelled"}:
                    raise BacktestValidationError(
                        "BACKTEST_PLATEAU_TASK_CONTROL_INVALID",
                        f"任务当前状态为 {task.status}，无法暂停。",
                    )
                if task.status != "paused":
                    task = task.model_copy(
                        update={
                            "status": "paused",
                            "progress": task.progress.model_copy(
                                update={
                                    "message": "任务已暂停。",
                                    "updated_at": now_text,
                                }
                            ),
                            "error": None,
                            "error_code": None,
                        }
                    )
                    self._backtest_plateau_tasks[task_id] = task
            elif action == "resume":
                if task.status in {"succeeded", "failed", "cancelled"}:
                    raise BacktestValidationError(
                        "BACKTEST_PLATEAU_TASK_CONTROL_INVALID",
                        f"任务当前状态为 {task.status}，无法继续执行。",
                    )
                if task.status == "paused":
                    payload_for_resume = self._backtest_plateau_task_payloads.get(task_id)
                    if payload_for_resume is None:
                        raise BacktestValidationError(
                            "BACKTEST_PLATEAU_TASK_RESUME_PAYLOAD_MISSING",
                            "任务参数缺失，无法继续执行。",
                        )
                    task = task.model_copy(
                        update={
                            "status": "pending",
                            "progress": task.progress.model_copy(
                                update={
                                    "message": "任务已恢复，等待继续执行。",
                                    "updated_at": now_text,
                                }
                            ),
                            "error": None,
                            "error_code": None,
                        }
                    )
                    self._backtest_plateau_tasks[task_id] = task
            else:
                if task.status not in {"succeeded", "failed", "cancelled"}:
                    task = task.model_copy(
                        update={
                            "status": "cancelled",
                            "progress": task.progress.model_copy(
                                update={
                                    "message": "任务已停止。",
                                    "updated_at": now_text,
                                }
                            ),
                            "error": None,
                            "error_code": None,
                        }
                    )
                    self._backtest_plateau_tasks[task_id] = task

        current = self.get_backtest_plateau_task(task_id)
        if current is None:
            raise BacktestValidationError("BACKTEST_PLATEAU_TASK_NOT_FOUND", "收益平原任务不存在")
        self._persist_backtest_plateau_task_state(force=current.status in {"paused", "cancelled"})
        if action == "resume" and payload_for_resume is not None:
            self._start_backtest_plateau_task_worker(task_id, payload_for_resume, resumed=True)
        return current

    def pause_backtest_plateau_task(self, task_id: str) -> BacktestPlateauTaskStatusResponse:
        return self._control_backtest_plateau_task(task_id, "pause")

    def resume_backtest_plateau_task(self, task_id: str) -> BacktestPlateauTaskStatusResponse:
        return self._control_backtest_plateau_task(task_id, "resume")

    def cancel_backtest_plateau_task(self, task_id: str) -> BacktestPlateauTaskStatusResponse:
        return self._control_backtest_plateau_task(task_id, "cancel")

    def _start_backtest_plateau_task_worker(
        self,
        task_id: str,
        payload: BacktestPlateauRunRequest,
        *,
        resumed: bool = False,
    ) -> None:
        with self._backtest_plateau_task_lock:
            if task_id in self._backtest_plateau_running_worker_ids:
                return
            self._backtest_plateau_running_worker_ids.add(task_id)

        def _worker() -> None:
            try:
                task = self.get_backtest_plateau_task(task_id)
                if task is None:
                    return
                self._await_backtest_plateau_task_runnable(task_id)
                task = self.get_backtest_plateau_task(task_id)
                if task is None:
                    return
                running_message = "收益平原任务执行中..." if not resumed else "服务重启后自动续跑中..."
                running_progress = task.progress.model_copy(
                    update={
                        "message": running_message,
                        "updated_at": self._now_datetime(),
                    }
                )
                self._upsert_backtest_plateau_task(
                    task.model_copy(
                        update={
                            "status": "running",
                            "progress": running_progress,
                            "error": None,
                            "error_code": None,
                        }
                    )
                )

                def _progress(processed_points: int, total_points: int, message: str) -> None:
                    self._await_backtest_plateau_task_runnable(task_id)
                    current_task = self.get_backtest_plateau_task(task_id)
                    if current_task is None:
                        return
                    total_safe = max(1, int(total_points), int(current_task.progress.total_points or 0))
                    processed_safe = max(0, min(int(processed_points), total_safe))
                    processed_safe = max(int(current_task.progress.processed_points or 0), processed_safe)
                    percent = round((processed_safe / total_safe) * 100.0, 2)
                    next_progress = current_task.progress.model_copy(
                        update={
                            "processed_points": processed_safe,
                            "total_points": total_safe,
                            "percent": percent,
                            "message": message,
                            "updated_at": self._now_datetime(),
                        }
                    )
                    self._upsert_backtest_plateau_task(
                        current_task.model_copy(update={"status": "running", "progress": next_progress})
                    )

                result = self.run_backtest_plateau(
                    payload,
                    task_id=task_id,
                    progress_callback=_progress,
                    control_callback=lambda: self._await_backtest_plateau_task_runnable(task_id),
                )
                finished_task = self.get_backtest_plateau_task(task_id)
                if finished_task is None:
                    return
                done_progress = finished_task.progress.model_copy(
                    update={
                        "percent": 100.0,
                        "processed_points": max(
                            int(finished_task.progress.processed_points or 0),
                            int(finished_task.progress.total_points or 0),
                        ),
                        "message": "收益平原评估完成。",
                        "updated_at": self._now_datetime(),
                    }
                )
                self._upsert_backtest_plateau_task(
                    finished_task.model_copy(
                        update={
                            "status": "succeeded",
                            "progress": done_progress,
                            "result": result,
                            "error": None,
                            "error_code": None,
                        }
                    )
                )
            except BacktestTaskCancelledError:
                cancelled_task = self.get_backtest_plateau_task(task_id)
                if cancelled_task is None:
                    return
                if cancelled_task.status == "cancelled":
                    return
                cancelled_progress = cancelled_task.progress.model_copy(
                    update={
                        "message": "任务已停止。",
                        "updated_at": self._now_datetime(),
                    }
                )
                self._upsert_backtest_plateau_task(
                    cancelled_task.model_copy(
                        update={
                            "status": "cancelled",
                            "progress": cancelled_progress,
                            "error": None,
                            "error_code": None,
                        }
                    )
                )
            except Exception as exc:  # noqa: BLE001
                failed_task = self.get_backtest_plateau_task(task_id)
                if failed_task is None:
                    return
                failed_progress = failed_task.progress.model_copy(
                    update={
                        "message": "收益平原任务失败。",
                        "updated_at": self._now_datetime(),
                    }
                )
                error_code = "BACKTEST_PLATEAU_TASK_FAILED"
                if isinstance(exc, BacktestValidationError):
                    error_code = exc.code
                self._upsert_backtest_plateau_task(
                    failed_task.model_copy(
                        update={
                            "status": "failed",
                            "progress": failed_progress,
                            "error": str(exc),
                            "error_code": error_code,
                        }
                    )
                )
            finally:
                with self._backtest_plateau_task_lock:
                    self._backtest_plateau_running_worker_ids.discard(task_id)
                self.maybe_trim_backtest_runtime_memory()
                self._persist_backtest_plateau_task_state(force=True)

        try:
            Thread(target=keep_awake_while(_worker), daemon=True).start()
        except Exception as exc:
            with self._backtest_plateau_task_lock:
                self._backtest_plateau_running_worker_ids.discard(task_id)
            failed_task = self.get_backtest_plateau_task(task_id)
            if failed_task is not None and failed_task.status not in {"succeeded", "failed", "cancelled"}:
                failed_progress = failed_task.progress.model_copy(
                    update={
                        "message": "收益平原任务启动失败。",
                        "updated_at": self._now_datetime(),
                    }
                )
                self._upsert_backtest_plateau_task(
                    failed_task.model_copy(
                        update={
                            "status": "failed",
                            "progress": failed_progress,
                            "error": f"任务线程启动失败：{exc}",
                            "error_code": "BACKTEST_PLATEAU_TASK_WORKER_START_FAILED",
                        }
                    )
                )
            self._persist_backtest_plateau_task_state(force=True)

    def start_backtest_plateau_task(self, payload: BacktestPlateauRunRequest) -> str:
        base_payload, _profile, _profile_id, _profile_hash = self._bind_backtest_event_judgment_profile(
            payload.base_payload
        )
        if base_payload != payload.base_payload:
            payload = payload.model_copy(update={"base_payload": base_payload}, deep=True)
        task_id = f"bp_{uuid4().hex[:16]}"
        now_text = self._now_datetime()
        total_points = self._estimate_backtest_plateau_total_points(payload)
        warning: str | None = None
        if total_points >= 150:
            warning = "收益平原评估参数较多，耗时可能较久，请耐心等待。"

        initial_progress = BacktestPlateauTaskProgress(
            sampling_mode=payload.sampling_mode,
            processed_points=0,
            total_points=total_points,
            percent=0.0,
            message="任务已创建，等待执行。",
            started_at=now_text,
            updated_at=now_text,
        )
        if warning:
            initial_progress = initial_progress.model_copy(
                update={"message": warning},
            )
        with self._backtest_plateau_task_lock:
            self._backtest_plateau_task_payloads[task_id] = payload.model_copy(deep=True)
        self._upsert_backtest_plateau_task(
            BacktestPlateauTaskStatusResponse(
                task_id=task_id,
                status="pending",
                progress=initial_progress,
                result=None,
                error=None,
                error_code=None,
            )
        )
        self._start_backtest_plateau_task_worker(
            task_id,
            payload,
            resumed=False,
        )
        return task_id

    @staticmethod
    def _normalize_signal_etf_symbol(raw: object) -> str:
        text = str(raw or "").strip().lower()
        if re.fullmatch(r"(sh|sz|bj)\d{6}", text):
            return text
        return ""

    @staticmethod
    def _safe_signal_etf_date(raw: object, fallback: str) -> str:
        text = str(raw or "").strip()
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            return text
        return fallback

    @staticmethod
    def _signal_etf_limit_up_ratio(symbol: str) -> float:
        code = str(symbol or "")[2:]
        market = str(symbol or "")[:2]
        if market == "bj":
            return 0.30
        if code.startswith(("300", "301", "688", "689")):
            return 0.20
        return 0.10

    @staticmethod
    def _to_signal_etf_ratio(numerator: float, denominator: float) -> float:
        if not (math.isfinite(numerator) and math.isfinite(denominator) and denominator > 0):
            return 0.0
        return float(numerator / denominator)

    @staticmethod
    def _round_signal_etf_ratio(value: float) -> float:
        if not math.isfinite(value):
            return 0.0
        return round(float(value), 6)

    def _build_signal_etf_unique_name(self, base_name: str, *, exclude_record_id: str | None = None) -> str:
        normalized_base = str(base_name or "").strip() or "待买ETF"
        used_names = {
            str(row.get("name", "")).strip().lower()
            for rid, row in self._signal_etf_backtest_store.items()
            if rid != exclude_record_id
        }
        if normalized_base.lower() not in used_names:
            return normalized_base
        index = 2
        while True:
            candidate = f"{normalized_base}-{index}"
            if candidate.lower() not in used_names:
                return candidate
            index += 1

    def _normalize_signal_etf_constituents(
        self,
        raw_items: object,
        *,
        fallback_signal_date: str,
    ) -> list[SignalEtfBacktestConstituentInput]:
        if not isinstance(raw_items, list):
            return []
        out: list[SignalEtfBacktestConstituentInput] = []
        seen: set[str] = set()
        for row in raw_items:
            candidate: SignalEtfBacktestConstituentInput | None = None
            if isinstance(row, SignalEtfBacktestConstituentInput):
                candidate = row
            elif isinstance(row, dict):
                try:
                    candidate = SignalEtfBacktestConstituentInput(**row)
                except Exception:
                    candidate = None
            if candidate is None:
                continue
            symbol = self._normalize_signal_etf_symbol(candidate.symbol)
            if not symbol or symbol in seen:
                continue
            seen.add(symbol)
            normalized_signal_date = self._safe_signal_etf_date(candidate.signal_date, fallback_signal_date)
            out.append(
                SignalEtfBacktestConstituentInput(
                    symbol=symbol,
                    name=str(candidate.name or "").strip()[:64],
                    signal_date=normalized_signal_date,
                    signal_primary=str(candidate.signal_primary or "").strip()[:16],
                    signal_event=str(candidate.signal_event or "").strip()[:64],
                    signal_reason=str(candidate.signal_reason or "").strip()[:400],
                )
            )
        return out

    def _signal_etf_window_bars(self, signal_dates: list[str]) -> int:
        today_text = self._now_date()
        fallback = max(400, int(self._config.candles_window_bars))
        date_points: list[datetime] = []
        for item in signal_dates:
            text = str(item or "").strip()
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
                continue
            try:
                date_points.append(datetime.strptime(text, "%Y-%m-%d"))
            except Exception:
                continue
        if not date_points:
            return fallback
        try:
            today_dt = datetime.strptime(today_text, "%Y-%m-%d")
        except Exception:
            return fallback
        oldest = min(date_points)
        days = max(60, (today_dt - oldest).days + 30)
        dynamic = max(fallback, days * 2)
        return max(400, min(5000, int(dynamic)))

    def _load_signal_etf_candles(
        self,
        symbol: str,
        *,
        window_bars: int,
        candle_cache: dict[str, list[CandlePoint]],
    ) -> list[CandlePoint]:
        key = str(symbol or "").strip().lower()
        cached = candle_cache.get(key)
        if cached is not None:
            return cached
        candles = load_candles_for_symbol(
            self._config.tdx_data_path,
            key,
            window=max(120, int(window_bars)),
            market_data_source=self._config.market_data_source,
            akshare_cache_dir=self._config.akshare_cache_dir,
        )
        resolved = list(candles or [])
        resolved.sort(key=lambda item: item.time)
        candle_cache[key] = resolved
        return resolved

    def _resolve_signal_etf_buy_price(
        self,
        *,
        symbol: str,
        candles: list[CandlePoint],
        buy_index: int,
    ) -> float:
        if buy_index < 0 or buy_index >= len(candles):
            return 0.0
        candle = candles[buy_index]
        open_price = float(candle.open)
        if buy_index <= 0:
            return round(open_price, 2)
        prev_close = float(candles[buy_index - 1].close)
        if prev_close <= 0:
            return round(open_price, 2)
        ratio = self._signal_etf_limit_up_ratio(symbol)
        limit_up_price = round(prev_close * (1.0 + ratio), 2)
        if open_price >= limit_up_price * 0.999:
            return limit_up_price
        return round(open_price, 2)

    @staticmethod
    def _resolve_close_on_or_before(
        *,
        dates: list[str],
        closes: list[float],
        target_date: str,
        min_date: str,
    ) -> float | None:
        if not dates or len(dates) != len(closes):
            return None
        index = bisect_right(dates, target_date) - 1
        if index < 0:
            return None
        if dates[index] < min_date:
            return None
        close = closes[index]
        if not (math.isfinite(close) and close > 0):
            return None
        return float(close)

    def _build_signal_etf_runtime(
        self,
        raw: dict[str, Any],
        *,
        candle_cache: dict[str, list[CandlePoint]],
        as_of_date: str | None = None,
        holding_days: int | None = None,
    ) -> dict[str, object]:
        now_date = self._now_date()
        now_datetime = self._now_datetime()
        valuation_cutoff = self._safe_signal_etf_date(as_of_date, now_date) if as_of_date else None
        record_id = str(raw.get("record_id") or "").strip() or f"setf_{uuid4().hex[:12]}"
        signal_date = self._safe_signal_etf_date(raw.get("signal_date"), now_date)
        strategy_id = str(raw.get("strategy_id") or "").strip() or "unknown_strategy"
        strategy_name = str(raw.get("strategy_name") or "").strip() or strategy_id
        base_name = str(raw.get("name") or "").strip() or f"{strategy_name}_{signal_date}"
        notes = str(raw.get("notes") or "").strip()
        benchmark_symbol_raw = self._normalize_signal_etf_symbol(raw.get("benchmark_symbol")) or ""
        if not benchmark_symbol_raw or benchmark_symbol_raw == "sh000300":
            benchmark_symbol = "sh000001"
        else:
            benchmark_symbol = benchmark_symbol_raw
        created_at = str(raw.get("created_at") or "").strip() or now_datetime
        updated_at = str(raw.get("updated_at") or "").strip() or created_at
        normalized_holding_days = max(1, int(holding_days)) if holding_days is not None else None

        constituents = self._normalize_signal_etf_constituents(
            raw.get("constituents"),
            fallback_signal_date=signal_date,
        )
        window_bars = self._signal_etf_window_bars(
            [signal_date, *[item.signal_date for item in constituents]],
        )

        stock_frames: dict[str, dict[str, object]] = {}
        all_symbol_dates: set[str] = set()
        for item in constituents:
            symbol = item.symbol
            candles = self._load_signal_etf_candles(
                symbol,
                window_bars=window_bars,
                candle_cache=candle_cache,
            )
            dates = [row.time for row in candles]
            closes = [float(row.close) for row in candles]
            date_to_index = {day: idx for idx, day in enumerate(dates)}
            if valuation_cutoff and dates:
                latest_index = bisect_right(dates, valuation_cutoff) - 1
                if latest_index >= 0:
                    latest_date = dates[latest_index]
                    latest_close = closes[latest_index] if latest_index < len(closes) else None
                else:
                    latest_date = None
                    latest_close = None
            else:
                latest_date = dates[-1] if dates else None
                latest_close = closes[-1] if closes else None
            stock_frames[symbol] = {
                "candles": candles,
                "dates": dates,
                "closes": closes,
                "date_to_index": date_to_index,
                "latest_date": latest_date,
                "latest_close": latest_close,
            }
            all_symbol_dates.update(dates)

        benchmark_candles = self._load_signal_etf_candles(
            benchmark_symbol,
            window_bars=window_bars,
            candle_cache=candle_cache,
        )
        benchmark_dates = [row.time for row in benchmark_candles]
        benchmark_closes = [float(row.close) for row in benchmark_candles]
        benchmark_available = len(benchmark_dates) > 0 and len(benchmark_dates) == len(benchmark_closes)
        calendar_dates = benchmark_dates if benchmark_available else sorted(all_symbol_dates)

        def _resolve_buy_date(reference_date: str, offset: int) -> str | None:
            if not calendar_dates:
                return None
            base_idx = bisect_right(calendar_dates, reference_date)
            target = base_idx + max(0, int(offset) - 1)
            if target < 0 or target >= len(calendar_dates):
                return None
            return calendar_dates[target]

        t1_positions: dict[str, tuple[str, float]] = {}
        t2_positions: dict[str, tuple[str, float]] = {}
        constituent_details: list[SignalEtfBacktestConstituentDetail] = []

        for item in constituents:
            frame = stock_frames.get(item.symbol) or {}
            dates = frame.get("dates") if isinstance(frame, dict) else None
            closes = frame.get("closes") if isinstance(frame, dict) else None
            candles = frame.get("candles") if isinstance(frame, dict) else None
            date_to_index = frame.get("date_to_index") if isinstance(frame, dict) else None
            latest_date = frame.get("latest_date") if isinstance(frame, dict) else None
            latest_close = frame.get("latest_close") if isinstance(frame, dict) else None
            dates = dates if isinstance(dates, list) else []
            closes = closes if isinstance(closes, list) else []
            candles = candles if isinstance(candles, list) else []
            date_to_index = date_to_index if isinstance(date_to_index, dict) else {}
            current_date = str(latest_date).strip() if isinstance(latest_date, str) and latest_date else None
            current_price = (
                round(float(latest_close), 2)
                if isinstance(latest_close, (int, float)) and math.isfinite(float(latest_close)) and float(latest_close) > 0
                else None
            )

            buy_date_t1 = _resolve_buy_date(item.signal_date, 1)
            buy_idx_t1 = date_to_index.get(buy_date_t1) if buy_date_t1 else None
            buy_price_t1: float | None = None
            status_t1: Literal["bought", "skipped"] = "skipped"
            if isinstance(buy_idx_t1, int) and buy_idx_t1 >= 0:
                resolved_price = self._resolve_signal_etf_buy_price(
                    symbol=item.symbol,
                    candles=candles,
                    buy_index=buy_idx_t1,
                )
                if resolved_price > 0:
                    buy_price_t1 = resolved_price
                    t1_positions[item.symbol] = (buy_date_t1 or "", buy_price_t1)
                    status_t1 = "bought"

            buy_date_t2 = _resolve_buy_date(item.signal_date, 2)
            buy_idx_t2 = date_to_index.get(buy_date_t2) if buy_date_t2 else None
            buy_price_t2: float | None = None
            status_t2: Literal["bought", "skipped"] = "skipped"
            if isinstance(buy_idx_t2, int) and buy_idx_t2 >= 0:
                resolved_price = self._resolve_signal_etf_buy_price(
                    symbol=item.symbol,
                    candles=candles,
                    buy_index=buy_idx_t2,
                )
                if resolved_price > 0:
                    buy_price_t2 = resolved_price
                    t2_positions[item.symbol] = (buy_date_t2 or "", buy_price_t2)
                    status_t2 = "bought"

            holding_target_date = (
                _resolve_buy_date(item.signal_date, normalized_holding_days)
                if normalized_holding_days is not None
                else None
            )
            holding_buy_date = (
                _resolve_buy_date(item.signal_date, 1)
                if normalized_holding_days is not None
                else None
            )
            holding_buy_idx = date_to_index.get(holding_buy_date) if holding_buy_date else None
            holding_buy_price: float | None = None
            if isinstance(holding_buy_idx, int) and holding_buy_idx >= 0:
                resolved_holding_buy_price = self._resolve_signal_etf_buy_price(
                    symbol=item.symbol,
                    candles=candles,
                    buy_index=holding_buy_idx,
                )
                if resolved_holding_buy_price > 0:
                    holding_buy_price = resolved_holding_buy_price

            return_pct_holding = None
            if (
                normalized_holding_days is not None
                and holding_target_date
                and holding_buy_date
                and holding_buy_price is not None
                and holding_buy_price > 0
                and holding_target_date >= holding_buy_date
            ):
                holding_close = self._resolve_close_on_or_before(
                    dates=dates,
                    closes=closes,
                    target_date=holding_target_date,
                    min_date=holding_buy_date,
                )
                if holding_close is not None and holding_close > 0:
                    return_pct_holding = self._round_signal_etf_ratio((holding_close / holding_buy_price) - 1.0)

            return_pct_t1 = (
                self._round_signal_etf_ratio((float(current_price) / buy_price_t1) - 1.0)
                if (
                    current_price is not None
                    and buy_price_t1 is not None
                    and buy_price_t1 > 0
                    and isinstance(current_date, str)
                    and isinstance(buy_date_t1, str)
                    and current_date >= buy_date_t1
                )
                else None
            )
            return_pct_t2 = (
                self._round_signal_etf_ratio((float(current_price) / buy_price_t2) - 1.0)
                if (
                    current_price is not None
                    and buy_price_t2 is not None
                    and buy_price_t2 > 0
                    and isinstance(current_date, str)
                    and isinstance(buy_date_t2, str)
                    and current_date >= buy_date_t2
                )
                else None
            )
            constituent_details.append(
                SignalEtfBacktestConstituentDetail(
                    symbol=item.symbol,
                    name=item.name,
                    signal_date=item.signal_date,
                    signal_primary=item.signal_primary,
                    signal_event=item.signal_event,
                    signal_reason=item.signal_reason,
                    current_date=current_date,
                    current_price=current_price,
                    buy_date_t1=buy_date_t1 if status_t1 == "bought" else None,
                    buy_price_t1=buy_price_t1,
                    return_pct_t1=return_pct_t1,
                    status_t1=status_t1,
                    buy_date_t2=buy_date_t2 if status_t2 == "bought" else None,
                    buy_price_t2=buy_price_t2,
                    return_pct_t2=return_pct_t2,
                    status_t2=status_t2,
                    holding_period_days=normalized_holding_days,
                    holding_target_date=holding_target_date if normalized_holding_days is not None else None,
                    return_pct_holding=return_pct_holding,
                )
            )

        def _build_etf_series(
            positions: dict[str, tuple[str, float]],
        ) -> tuple[dict[str, float], dict[str, float]]:
            if not positions or not calendar_dates:
                return {}, {}
            series: dict[str, float] = {}
            per_stock_final: dict[str, float] = {}
            for day in calendar_dates:
                if valuation_cutoff and day > valuation_cutoff:
                    break
                returns: list[float] = []
                for symbol, (buy_day, buy_price) in positions.items():
                    if not buy_day or buy_day > day or buy_price <= 0:
                        continue
                    frame = stock_frames.get(symbol) or {}
                    dates = frame.get("dates") if isinstance(frame, dict) else None
                    closes = frame.get("closes") if isinstance(frame, dict) else None
                    dates = dates if isinstance(dates, list) else []
                    closes = closes if isinstance(closes, list) else []
                    close = self._resolve_close_on_or_before(
                        dates=dates,
                        closes=closes,
                        target_date=day,
                        min_date=buy_day,
                    )
                    if close is None or close <= 0:
                        continue
                    returns.append((close / buy_price) - 1.0)
                if returns:
                    series[day] = self._round_signal_etf_ratio(sum(returns) / len(returns))
            if series:
                last_day = next(reversed(series))
                for symbol, (buy_day, buy_price) in positions.items():
                    if not buy_day or buy_price <= 0:
                        continue
                    frame = stock_frames.get(symbol) or {}
                    dates = frame.get("dates") if isinstance(frame, dict) else None
                    closes = frame.get("closes") if isinstance(frame, dict) else None
                    dates = dates if isinstance(dates, list) else []
                    closes = closes if isinstance(closes, list) else []
                    close = self._resolve_close_on_or_before(
                        dates=dates,
                        closes=closes,
                        target_date=last_day,
                        min_date=buy_day,
                    )
                    if close is None or close <= 0:
                        continue
                    per_stock_final[symbol] = self._round_signal_etf_ratio((close / buy_price) - 1.0)
            return series, per_stock_final

        def _build_benchmark_series(start_date: str | None) -> dict[str, float]:
            if not start_date or not benchmark_available:
                return {}
            if not benchmark_dates or len(benchmark_dates) != len(benchmark_closes):
                return {}
            start_close = self._resolve_close_on_or_before(
                dates=benchmark_dates,
                closes=benchmark_closes,
                target_date=start_date,
                min_date=benchmark_dates[0],
            )
            if start_close is None or start_close <= 0:
                return {}
            out: dict[str, float] = {}
            for day in calendar_dates:
                if valuation_cutoff and day > valuation_cutoff:
                    break
                if day < start_date:
                    continue
                close = self._resolve_close_on_or_before(
                    dates=benchmark_dates,
                    closes=benchmark_closes,
                    target_date=day,
                    min_date=benchmark_dates[0],
                )
                if close is None or close <= 0:
                    continue
                out[day] = self._round_signal_etf_ratio((close / start_close) - 1.0)
            return out

        t1_series, t1_stock_final = _build_etf_series(t1_positions)
        t2_series, t2_stock_final = _build_etf_series(t2_positions)
        t1_start = next(iter(t1_series.keys()), None) if t1_series else None
        t2_start = next(iter(t2_series.keys()), None) if t2_series else None
        t1_benchmark_series = _build_benchmark_series(t1_start)
        t2_benchmark_series = _build_benchmark_series(t2_start)
        holding_target_date_summary: str | None = None
        holding_return_pct_summary: float | None = None
        if normalized_holding_days is not None and t1_series:
            requested_target_date = _resolve_buy_date(signal_date, normalized_holding_days)
            if requested_target_date:
                eligible_dates = [day for day in t1_series.keys() if day <= requested_target_date]
                if eligible_dates:
                    resolved_target_date = eligible_dates[-1]
                    holding_target_date_summary = resolved_target_date
                    resolved_return = t1_series.get(resolved_target_date)
                    if isinstance(resolved_return, (int, float)) and math.isfinite(float(resolved_return)):
                        holding_return_pct_summary = self._round_signal_etf_ratio(float(resolved_return))

        def _build_performance(
            series: dict[str, float],
            stock_final: dict[str, float],
            benchmark_series: dict[str, float],
            *,
            total_count: int,
        ) -> SignalEtfBacktestPerformance:
            if not series:
                return SignalEtfBacktestPerformance(
                    return_pct=0.0,
                    benchmark_return_pct=0.0,
                    excess_return_pct=0.0,
                    stock_win_rate=0.0,
                    daily_win_rate=0.0,
                    tradable_count=0,
                    skipped_count=total_count,
                )
            series_values = list(series.values())
            final_return = series_values[-1] if series_values else 0.0
            daily_win_days = len([value for value in series_values if value > 0])
            daily_win_rate = self._to_signal_etf_ratio(daily_win_days, len(series_values))
            tradable_count = len(stock_final)
            stock_win_count = len([value for value in stock_final.values() if value > 0])
            stock_win_rate = self._to_signal_etf_ratio(stock_win_count, tradable_count)
            benchmark_values = list(benchmark_series.values())
            benchmark_final = benchmark_values[-1] if benchmark_values else 0.0
            return SignalEtfBacktestPerformance(
                return_pct=self._round_signal_etf_ratio(final_return),
                benchmark_return_pct=self._round_signal_etf_ratio(benchmark_final),
                excess_return_pct=self._round_signal_etf_ratio(final_return - benchmark_final),
                stock_win_rate=self._round_signal_etf_ratio(stock_win_rate),
                daily_win_rate=self._round_signal_etf_ratio(daily_win_rate),
                tradable_count=tradable_count,
                skipped_count=max(0, total_count - tradable_count),
            )

        summary = SignalEtfBacktestSummary(
            t1=_build_performance(
                t1_series,
                t1_stock_final,
                t1_benchmark_series,
                total_count=len(constituents),
            ),
            t2=_build_performance(
                t2_series,
                t2_stock_final,
                t2_benchmark_series,
                total_count=len(constituents),
            ),
            strategy_stats=SignalEtfBacktestStrategyStats(strategy_id=strategy_id),
            holding_period_days=normalized_holding_days,
            holding_target_date=holding_target_date_summary,
            holding_return_pct=holding_return_pct_summary,
        )

        merged_curve_dates = sorted(
            set(t1_series.keys()) | set(t2_series.keys()) | set(t1_benchmark_series.keys()) | set(t2_benchmark_series.keys())
        )
        curve: list[SignalEtfBacktestCurvePoint] = []
        for day in merged_curve_dates:
            etf_t1 = t1_series.get(day)
            etf_t2 = t2_series.get(day)
            benchmark_t1 = t1_benchmark_series.get(day)
            benchmark_t2 = t2_benchmark_series.get(day)
            curve.append(
                SignalEtfBacktestCurvePoint(
                    date=day,
                    etf_return_t1=etf_t1,
                    etf_return_t2=etf_t2,
                    benchmark_return_t1=benchmark_t1,
                    benchmark_return_t2=benchmark_t2,
                    excess_return_t1=(
                        self._round_signal_etf_ratio(etf_t1 - benchmark_t1)
                        if etf_t1 is not None and benchmark_t1 is not None
                        else None
                    ),
                    excess_return_t2=(
                        self._round_signal_etf_ratio(etf_t2 - benchmark_t2)
                        if etf_t2 is not None and benchmark_t2 is not None
                        else None
                    ),
                )
            )

        detail = SignalEtfBacktestDetail(
            record_id=record_id,
            name=base_name,
            notes=notes,
            signal_date=signal_date,
            strategy_id=strategy_id,
            strategy_name=strategy_name,
            benchmark_symbol=benchmark_symbol,
            total_constituents=len(constituents),
            created_at=created_at,
            updated_at=updated_at,
            summary=summary,
            benchmark_available=benchmark_available,
            constituents=constituent_details,
            curve=curve,
        )
        record = SignalEtfBacktestRecord(
            record_id=detail.record_id,
            name=detail.name,
            notes=detail.notes,
            signal_date=detail.signal_date,
            strategy_id=detail.strategy_id,
            strategy_name=detail.strategy_name,
            benchmark_symbol=detail.benchmark_symbol,
            total_constituents=detail.total_constituents,
            created_at=detail.created_at,
            updated_at=detail.updated_at,
            summary=detail.summary,
        )
        return {
            "record_id": record_id,
            "strategy_id": strategy_id,
            "t1_positive": bool(record.summary.t1.return_pct > 0),
            "t2_positive": bool(record.summary.t2.return_pct > 0),
            "record": record,
            "detail": detail,
        }

    @staticmethod
    def _build_signal_etf_strategy_stats_map(
        runtimes: list[dict[str, object]],
    ) -> dict[str, SignalEtfBacktestStrategyStats]:
        counters: dict[str, dict[str, int]] = {}
        for runtime in runtimes:
            strategy_id = str(runtime.get("strategy_id") or "").strip() or "unknown_strategy"
            bucket = counters.setdefault(strategy_id, {"total": 0, "wins_t1": 0, "wins_t2": 0})
            bucket["total"] += 1
            if bool(runtime.get("t1_positive")):
                bucket["wins_t1"] += 1
            if bool(runtime.get("t2_positive")):
                bucket["wins_t2"] += 1
        out: dict[str, SignalEtfBacktestStrategyStats] = {}
        for strategy_id, bucket in counters.items():
            total = max(0, int(bucket.get("total", 0)))
            wins_t1 = max(0, int(bucket.get("wins_t1", 0)))
            wins_t2 = max(0, int(bucket.get("wins_t2", 0)))
            out[strategy_id] = SignalEtfBacktestStrategyStats(
                strategy_id=strategy_id,
                total_records=total,
                win_rate_t1=round((wins_t1 / total), 6) if total > 0 else 0.0,
                win_rate_t2=round((wins_t2 / total), 6) if total > 0 else 0.0,
            )
        return out

    def _build_signal_etf_runtime_bundle(
        self,
        *,
        as_of_date: str | None = None,
        holding_days: int | None = None,
    ) -> tuple[list[SignalEtfBacktestRecord], dict[str, SignalEtfBacktestDetail]]:
        with self._lock:
            raw_rows = [dict(value) for value in self._signal_etf_backtest_store.values() if isinstance(value, dict)]
        candle_cache: dict[str, list[CandlePoint]] = {}
        runtimes = [
            self._build_signal_etf_runtime(
                row,
                candle_cache=candle_cache,
                as_of_date=as_of_date,
                holding_days=holding_days,
            )
            for row in raw_rows
        ]
        stats_map = self._build_signal_etf_strategy_stats_map(runtimes)

        records: list[SignalEtfBacktestRecord] = []
        detail_map: dict[str, SignalEtfBacktestDetail] = {}
        for runtime in runtimes:
            record = runtime.get("record")
            detail = runtime.get("detail")
            strategy_id = str(runtime.get("strategy_id") or "").strip() or "unknown_strategy"
            if not isinstance(record, SignalEtfBacktestRecord) or not isinstance(detail, SignalEtfBacktestDetail):
                continue
            strategy_stats = stats_map.get(strategy_id, SignalEtfBacktestStrategyStats(strategy_id=strategy_id))
            patched_summary = record.summary.model_copy(update={"strategy_stats": strategy_stats})
            patched_record = record.model_copy(update={"summary": patched_summary})
            patched_detail = detail.model_copy(update={"summary": patched_summary})
            records.append(patched_record)
            detail_map[patched_record.record_id] = patched_detail

        records.sort(key=lambda item: item.created_at, reverse=True)
        return records, detail_map

    @state_locked
    def _create_signal_etf_backtest_row(
        self,
        *,
        strategy_id: str,
        strategy_name: str,
        signal_date: str,
        name: str | None,
        notes: str | None,
        constituents: list[SignalEtfBacktestConstituentInput],
        persist: bool,
    ) -> tuple[str, str]:
        if len(constituents) <= 0:
            raise BacktestValidationError("SIGNAL_ETF_EMPTY", "没有可用成分股，无法生成待买ETF回测。")

        custom_name = str(name or "").strip()
        base_name = custom_name or f"{strategy_name}_{signal_date}"
        now_text = self._now_datetime()
        record_id = f"setf_{uuid4().hex[:16]}"

        with self._lock:
            unique_name = self._build_signal_etf_unique_name(base_name)
            self._signal_etf_backtest_store[record_id] = {
                "record_id": record_id,
                "name": unique_name,
                "notes": str(notes or "").strip(),
                "signal_date": signal_date,
                "strategy_id": strategy_id,
                "strategy_name": strategy_name,
                "benchmark_symbol": "sh000001",
                "created_at": now_text,
                "updated_at": now_text,
                "constituents": [item.model_dump(exclude_none=True) for item in constituents],
            }
        if persist:
            self._persist_app_state()
        return record_id, unique_name

    def _build_signal_etf_constituents_from_signals(
        self,
        items: list[SignalResult],
        *,
        fallback_signal_date: str,
    ) -> list[SignalEtfBacktestConstituentInput]:
        raw_rows: list[dict[str, object]] = []
        for row in items:
            raw_signal_date = str(row.trigger_date or "").strip()
            signal_date = self._safe_signal_etf_date(raw_signal_date, fallback_signal_date)
            signal_event = str(row.wyckoff_signal or "").strip()
            if not signal_event and isinstance(row.wy_events, list) and row.wy_events:
                signal_event = str(row.wy_events[-1] or "").strip()
            raw_rows.append(
                {
                    "symbol": str(row.symbol or "").strip().lower(),
                    "name": str(row.name or "").strip(),
                    "signal_date": signal_date,
                    "signal_primary": str(row.primary_signal or "").strip(),
                    "signal_event": signal_event,
                    "signal_reason": str(row.trigger_reason or "").strip(),
                }
            )
        return self._normalize_signal_etf_constituents(raw_rows, fallback_signal_date=fallback_signal_date)

    def create_signal_etf_backtest(self, payload: SignalEtfBacktestCreateRequest) -> SignalEtfBacktestDetail:
        strategy_id = str(payload.strategy_id or "").strip()
        if not strategy_id:
            raise BacktestValidationError("SIGNAL_ETF_STRATEGY_REQUIRED", "strategy_id 不能为空。")
        signal_date = self._safe_signal_etf_date(payload.signal_date, self._now_date())
        strategy_name = str(payload.strategy_name or "").strip() or strategy_id
        normalized_constituents = self._normalize_signal_etf_constituents(
            [item.model_dump(exclude_none=True) for item in payload.constituents],
            fallback_signal_date=signal_date,
        )
        record_id, _ = self._create_signal_etf_backtest_row(
            strategy_id=strategy_id,
            strategy_name=strategy_name,
            signal_date=signal_date,
            name=payload.name,
            notes=payload.notes,
            constituents=normalized_constituents,
            persist=True,
        )
        detail = self.get_signal_etf_backtest(record_id, refresh=True)
        if detail is None:
            raise BacktestValidationError("SIGNAL_ETF_NOT_FOUND", "待买ETF回测记录创建失败。")
        return detail

    @state_locked
    def create_signal_etf_backtests_auto(
        self,
        payload: SignalEtfBacktestAutoCreateRequest,
    ) -> SignalEtfBacktestAutoCreateResponse:
        scan_mode: SignalScanMode = (
            "full_market" if str(payload.mode or "").strip() == "full_market" else "trend_pool"
        )
        run_id = str(payload.run_id or "").strip()
        if scan_mode == "trend_pool":
            if not run_id:
                raise BacktestValidationError("SIGNAL_ETF_RUN_REQUIRED", "趋势池模式下 run_id 不能为空。")
            _ = self._require_backtest_trend_pool_run(run_id)
        else:
            run_id = ""

        scan_dates = self._build_backtest_scan_dates(payload.date_from, payload.date_to)
        if not scan_dates:
            raise BacktestValidationError("SIGNAL_ETF_DATE_RANGE_INVALID", "日期区间无效。")
        if len(scan_dates) > 366:
            raise BacktestValidationError("SIGNAL_ETF_DATE_RANGE_TOO_LARGE", "日期区间最多支持 366 个交易日。")

        strategy_meta, normalized_strategy_params, _strategy_params_hash = self._resolve_strategy_runtime(
            strategy_id=payload.strategy_id,
            strategy_params=payload.strategy_params,
        )
        strategy_id = str(strategy_meta.get("strategy_id") or payload.strategy_id).strip() or "unknown_strategy"
        strategy_name = str(payload.strategy_name or "").strip() or str(strategy_meta.get("name") or strategy_id)

        allowed_markets = {"sh", "sz", "bj"}
        normalized_market_filters = list(
            dict.fromkeys(item for item in (payload.market_filters or []) if item in allowed_markets)
        )
        allowed_board_filters = {"main", "gem", "star", "beijing", "st"}
        normalized_board_filters = list(
            dict.fromkeys(item for item in (payload.board_filters or []) if item in allowed_board_filters)
        )

        signal_age_min = max(0, int(payload.signal_age_min))
        signal_age_max = int(payload.signal_age_max) if payload.signal_age_max is not None else None
        if signal_age_max is not None:
            signal_age_max = max(0, signal_age_max)
            if signal_age_max < signal_age_min:
                signal_age_min, signal_age_max = signal_age_max, signal_age_min

        name_prefix = str(payload.name_prefix or "").strip()
        notes_prefix = str(payload.notes or "").strip()
        refresh_signals = bool(payload.refresh_signals)

        created_items: list[SignalEtfBacktestAutoCreateItem] = []
        skipped_items: list[SignalEtfBacktestAutoCreateIssue] = []
        failed_items: list[SignalEtfBacktestAutoCreateIssue] = []

        for as_of_date in scan_dates:
            try:
                signals = self.get_signals(
                    mode=scan_mode,
                    run_id=run_id if scan_mode == "trend_pool" else None,
                    trend_step=payload.trend_step if scan_mode == "trend_pool" else "auto",
                    strategy_id=strategy_id,
                    strategy_params=normalized_strategy_params,
                    market_filters=normalized_market_filters if scan_mode == "full_market" else None,
                    board_filters=normalized_board_filters,
                    as_of_date=as_of_date,
                    refresh=refresh_signals,
                    window_days=int(payload.window_days),
                    min_score=float(payload.min_score),
                    require_sequence=bool(payload.require_sequence),
                    min_event_count=int(payload.min_event_count),
                    signal_age_min=signal_age_min,
                    signal_age_max=signal_age_max,
                )
            except BacktestValidationError as exc:
                failed_items.append(SignalEtfBacktestAutoCreateIssue(as_of_date=as_of_date, reason=f"{exc.code}:{exc}"))
                continue
            except ValueError as exc:
                failed_items.append(SignalEtfBacktestAutoCreateIssue(as_of_date=as_of_date, reason=str(exc)))
                continue

            if len(signals.items) <= 0:
                skipped_items.append(SignalEtfBacktestAutoCreateIssue(as_of_date=as_of_date, reason="NO_SIGNALS"))
                continue

            signal_date = self._safe_signal_etf_date(signals.as_of_date, as_of_date)
            constituents = self._build_signal_etf_constituents_from_signals(
                signals.items,
                fallback_signal_date=signal_date,
            )
            if len(constituents) <= 0:
                skipped_items.append(
                    SignalEtfBacktestAutoCreateIssue(as_of_date=as_of_date, reason="NO_VALID_CONSTITUENTS")
                )
                continue

            record_notes = notes_prefix
            if scan_mode == "full_market":
                auto_note = f"[auto] mode=full_market; as_of_date={as_of_date}"
            else:
                auto_note = f"[auto] run_id={run_id}; as_of_date={as_of_date}"
            if record_notes:
                record_notes = f"{record_notes}\n{auto_note}"
            else:
                record_notes = auto_note

            record_name = f"{name_prefix}_{signal_date}" if name_prefix else None
            record_id, record_unique_name = self._create_signal_etf_backtest_row(
                strategy_id=strategy_id,
                strategy_name=strategy_name,
                signal_date=signal_date,
                name=record_name,
                notes=record_notes,
                constituents=constituents,
                persist=False,
            )
            created_items.append(
                SignalEtfBacktestAutoCreateItem(
                    record_id=record_id,
                    name=record_unique_name,
                    signal_date=signal_date,
                    as_of_date=as_of_date,
                    total_constituents=len(constituents),
                )
            )

        if created_items:
            self._persist_app_state()

        return SignalEtfBacktestAutoCreateResponse(
            run_id=run_id,
            strategy_id=strategy_id,
            strategy_name=strategy_name,
            date_from=scan_dates[0],
            date_to=scan_dates[-1],
            processed_dates=len(scan_dates),
            created_count=len(created_items),
            skipped_count=len(skipped_items),
            failed_count=len(failed_items),
            created=created_items,
            skipped=skipped_items,
            failed=failed_items,
        )

    def list_signal_etf_backtests(
        self,
        *,
        refresh: bool = True,
        holding_days: int | None = None,
    ) -> SignalEtfBacktestListResponse:
        _ = refresh
        normalized_holding_days = max(1, int(holding_days)) if holding_days is not None else None
        records, _detail_map = self._build_signal_etf_runtime_bundle(holding_days=normalized_holding_days)
        return SignalEtfBacktestListResponse(items=records)

    def get_signal_etf_backtest(
        self,
        record_id: str,
        *,
        refresh: bool = True,
        as_of_date: str | None = None,
        holding_days: int | None = None,
    ) -> SignalEtfBacktestDetail | None:
        _ = refresh
        normalized = str(record_id or "").strip()
        if not normalized:
            return None
        valuation_date = self._safe_signal_etf_date(as_of_date, self._now_date()) if as_of_date else None
        normalized_holding_days = max(1, int(holding_days)) if holding_days is not None else None
        _records, detail_map = self._build_signal_etf_runtime_bundle(
            as_of_date=valuation_date,
            holding_days=normalized_holding_days,
        )
        return detail_map.get(normalized)

    @state_locked
    def update_signal_etf_backtest(
        self,
        record_id: str,
        payload: SignalEtfBacktestUpdateRequest,
    ) -> SignalEtfBacktestRecord:
        normalized = str(record_id or "").strip()
        if not normalized:
            raise BacktestValidationError("SIGNAL_ETF_NOT_FOUND", "待买ETF回测记录不存在。")
        with self._lock:
            target = self._signal_etf_backtest_store.get(normalized)
            if not isinstance(target, dict):
                raise BacktestValidationError("SIGNAL_ETF_NOT_FOUND", "待买ETF回测记录不存在。")
            updated = dict(target)
            if payload.name is not None:
                name_text = str(payload.name or "").strip()
                if not name_text:
                    raise BacktestValidationError("SIGNAL_ETF_NAME_EMPTY", "名称不能为空。")
                updated["name"] = self._build_signal_etf_unique_name(name_text, exclude_record_id=normalized)
            if payload.notes is not None:
                updated["notes"] = str(payload.notes or "").strip()
            updated["updated_at"] = self._now_datetime()
            self._signal_etf_backtest_store[normalized] = updated
        self._persist_app_state()
        records, _detail_map = self._build_signal_etf_runtime_bundle()
        for row in records:
            if row.record_id == normalized:
                return row
        raise BacktestValidationError("SIGNAL_ETF_NOT_FOUND", "待买ETF回测记录不存在。")

    @state_locked
    def delete_signal_etf_backtest(self, record_id: str) -> bool:
        normalized = str(record_id or "").strip()
        if not normalized:
            return False
        with self._lock:
            existed = normalized in self._signal_etf_backtest_store
            if existed:
                self._signal_etf_backtest_store.pop(normalized, None)
        if existed:
            self._persist_app_state()
        return existed

    def create_order(self, payload: CreateOrderRequest) -> CreateOrderResponse:
        return self._sim_engine.create_order(payload)

    def list_orders(
        self,
        *,
        status: Literal["pending", "filled", "cancelled", "rejected"] | None,
        symbol: str | None,
        side: Literal["buy", "sell"] | None,
        date_from: str | None,
        date_to: str | None,
        page: int,
        page_size: int,
    ) -> SimOrdersResponse:
        return self._sim_engine.list_orders(
            status=status,
            symbol=symbol,
            side=side,
            date_from=date_from,
            date_to=date_to,
            page=page,
            page_size=page_size,
        )

    def list_fills(
        self,
        *,
        symbol: str | None,
        side: Literal["buy", "sell"] | None,
        date_from: str | None,
        date_to: str | None,
        page: int,
        page_size: int,
    ) -> SimFillsResponse:
        return self._sim_engine.list_fills(
            symbol=symbol,
            side=side,
            date_from=date_from,
            date_to=date_to,
            page=page,
            page_size=page_size,
        )

    def cancel_order(self, order_id: str) -> CreateOrderResponse:
        return self._sim_engine.cancel_order(order_id)

    def settle_sim(self) -> SimSettleResponse:
        return self._sim_engine.settle()

    def reset_sim(self) -> SimResetResponse:
        return self._sim_engine.reset()

    def get_sim_config(self) -> SimTradingConfig:
        return self._sim_engine.get_config()

    def set_sim_config(self, payload: SimTradingConfig) -> SimTradingConfig:
        return self._sim_engine.set_config(payload)

    def get_portfolio(self) -> PortfolioSnapshot:
        return self._sim_engine.get_portfolio()

    def get_review(
        self,
        *,
        date_from: str | None = None,
        date_to: str | None = None,
        date_axis: Literal["sell", "buy"] = "sell",
    ) -> ReviewResponse:
        return self._sim_engine.get_review(
            date_from=date_from,
            date_to=date_to,
            date_axis=date_axis,
        )

    def get_daily_review(self, date: str) -> DailyReviewRecord | None:
        return self._daily_review_store.get(date)

    def list_daily_reviews(
        self,
        *,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> DailyReviewListResponse:
        rows = list(self._daily_review_store.values())
        if date_from:
            rows = [row for row in rows if row.date >= date_from]
        if date_to:
            rows = [row for row in rows if row.date <= date_to]
        rows.sort(key=lambda row: row.date, reverse=True)
        return DailyReviewListResponse(items=rows)

    @state_locked
    def upsert_daily_review(self, date: str, payload: DailyReviewPayload) -> DailyReviewRecord:
        body = payload.model_dump()
        body["tags"] = self._unique_ordered([str(item) for item in body.get("tags", [])])
        record = DailyReviewRecord(
            date=date,
            updated_at=self._now_datetime(),
            **body,
        )
        self._daily_review_store[date] = record
        self._persist_app_state()
        return record

    @state_locked
    def delete_daily_review(self, date: str) -> bool:
        if date not in self._daily_review_store:
            return False
        self._daily_review_store.pop(date, None)
        self._persist_app_state()
        return True

    def get_weekly_review(self, week_label: str) -> WeeklyReviewRecord | None:
        return self._weekly_review_store.get(week_label)

    def list_weekly_reviews(self, *, year: int | None = None) -> WeeklyReviewListResponse:
        rows = list(self._weekly_review_store.values())
        if year is not None:
            prefix = f"{year:04d}-W"
            rows = [row for row in rows if row.week_label.startswith(prefix)]
        rows.sort(key=lambda row: row.week_label, reverse=True)
        return WeeklyReviewListResponse(items=rows)

    @state_locked
    def upsert_weekly_review(self, week_label: str, payload: WeeklyReviewPayload) -> WeeklyReviewRecord:
        body = payload.model_dump()
        start_date = str(body.get("start_date", "")).strip()
        end_date = str(body.get("end_date", "")).strip()
        if not start_date or not end_date:
            start_date, end_date = self._resolve_week_range(week_label)
        body["start_date"] = start_date
        body["end_date"] = end_date
        body["tags"] = self._unique_ordered([str(item) for item in body.get("tags", [])])
        record = WeeklyReviewRecord(
            week_label=week_label,
            updated_at=self._now_datetime(),
            **body,
        )
        self._weekly_review_store[week_label] = record
        self._persist_app_state()
        return record

    @state_locked
    def delete_weekly_review(self, week_label: str) -> bool:
        if week_label not in self._weekly_review_store:
            return False
        self._weekly_review_store.pop(week_label, None)
        self._persist_app_state()
        return True

    def get_market_news(
        self,
        *,
        query: str = "",
        limit: int = 20,
        symbol: str | None = None,
        source_domains: list[str] | None = None,
        age_hours: int = 72,
        refresh: bool = False,
    ) -> MarketNewsResponse:
        max_items = max(1, min(int(limit), 50))
        max_age_hours = age_hours if age_hours in (24, 48, 72) else 72
        now_dt = datetime.now()
        cutoff_dt = now_dt - timedelta(hours=max_age_hours)

        symbol_text = TextProcessor.clean_whitespace(str(symbol or "")).lower()
        symbol_name = ""
        if symbol_text:
            profile = self._fetch_quote_profile(symbol_text)
            symbol_name = TextProcessor.clean_whitespace(profile.get("name", ""))
            if not query.strip():
                query = f"{symbol_text} {symbol_name} 新闻".strip()

        query_text = TextProcessor.clean_whitespace(query) or "A股 热点"
        default_domains = self._source_domains(self._enabled_ai_source_urls(limit=8))

        selected_domains: set[str] = set()
        for raw_domain in source_domains or []:
            token = TextProcessor.clean_whitespace(str(raw_domain))
            if not token:
                continue
            normalized = token
            if "://" in token:
                normalized = TextProcessor.extract_domain(token)
            normalized = normalized.lower().strip()
            if normalized.startswith("www."):
                normalized = normalized[4:]
            normalized = normalized.strip("/")
            if not normalized:
                continue
            selected_domains.add(normalized)
            root = TextProcessor.registrable_domain(normalized)
            if root:
                selected_domains.add(root)

        allowed_domains = selected_domains
        response_domains = sorted(selected_domains or default_domains)
        domain_key = ",".join(sorted(allowed_domains)) if allowed_domains else "*"
        cache_key = f"market_news:v3:{query_text}:{symbol_text}:{max_items}:{max_age_hours}:{domain_key}"
        now_ts = time.time()

        def parse_news_datetime(value: str) -> datetime | None:
            raw = TextProcessor.clean_whitespace(value)
            if not raw:
                return None
            parsed = self._parse_rss_pub_date(raw)
            if parsed is not None:
                return parsed

            candidate = raw
            candidate = candidate.replace("年", "-").replace("月", "-").replace("日", "")
            candidate = candidate.replace("/", "-").replace("T", " ")
            candidate = re.sub(r"\s+", " ", candidate).strip()

            patterns = [
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d %H:%M",
                "%m-%d %H:%M:%S",
                "%m-%d %H:%M",
                "%H:%M:%S",
                "%H:%M",
            ]
            for pattern in patterns:
                try:
                    parsed_dt = datetime.strptime(candidate, pattern)
                    if pattern.startswith("%m"):
                        parsed_dt = parsed_dt.replace(year=now_dt.year)
                    elif pattern.startswith("%H"):
                        parsed_dt = now_dt.replace(
                            hour=parsed_dt.hour,
                            minute=parsed_dt.minute,
                            second=parsed_dt.second,
                            microsecond=0,
                        )
                        if parsed_dt > now_dt + timedelta(minutes=5):
                            parsed_dt = parsed_dt - timedelta(days=1)
                    if parsed_dt > now_dt + timedelta(days=2):
                        parsed_dt = parsed_dt.replace(year=parsed_dt.year - 1)
                    return parsed_dt
                except Exception:
                    continue
            return None

        def to_pub_date(parsed_dt: datetime | None, raw_text: str) -> str:
            if parsed_dt is not None:
                return parsed_dt.strftime("%Y-%m-%d %H:%M:%S")
            return TextProcessor.clean_whitespace(raw_text)[:40]

        def is_recent(parsed_dt: datetime | None) -> bool:
            return parsed_dt is not None and parsed_dt >= cutoff_dt

        def filter_recent_items(rows: list[dict[str, str]]) -> list[dict[str, str]]:
            filtered: list[dict[str, str]] = []
            for item in rows:
                parsed_dt = parse_news_datetime(str(item.get("pub_date", "")))
                if not is_recent(parsed_dt):
                    continue
                filtered.append(item)
            filtered.sort(
                key=lambda item: parse_news_datetime(str(item.get("pub_date", ""))) or datetime.min,
                reverse=True,
            )
            return filtered[:max_items]

        def filter_relaxed_items(rows: list[dict[str, str]]) -> list[dict[str, str]]:
            filtered: list[dict[str, str]] = []
            for item in rows:
                parsed_dt = parse_news_datetime(str(item.get("pub_date", "")))
                if parsed_dt is None:
                    continue
                filtered.append(item)
            filtered.sort(
                key=lambda item: parse_news_datetime(str(item.get("pub_date", ""))) or datetime.min,
                reverse=True,
            )
            return filtered[:max_items]

        symbol_code = re.sub(r"^(sh|sz|bj)", "", symbol_text)
        default_query_tokens = {"a股", "热点", "a股热点", "a股 热点"}
        token_filters = [token.lower() for token in re.split(r"\s+", query_text) if token]
        if query_text.replace(" ", "").lower() in default_query_tokens:
            token_filters = []
        if symbol_text:
            token_filters.extend([symbol_text.lower(), symbol_code.lower()])
            if symbol_name:
                token_filters.append(symbol_name.lower())
        token_filters = [token for token in token_filters if token]

        cached = self._web_evidence_cache.get(cache_key)
        if (not refresh) and cached and now_ts - cached[0] <= 180:
            cached_items = filter_recent_items(cached[1])
            if not cached_items:
                cached_relaxed = filter_relaxed_items(cached[1])
                if cached_relaxed:
                    return MarketNewsResponse(
                        query=query_text,
                        age_hours=max_age_hours,
                        symbol=symbol_text or None,
                        symbol_name=symbol_name or None,
                        source_domains=response_domains,
                        items=[MarketNewsItem(**item) for item in cached_relaxed],
                        fetched_at=self._now_datetime(),
                        cache_hit=True,
                        fallback_used=True,
                        degraded=True,
                        degraded_reason="NEWS_OUT_OF_WINDOW",
                    )
            return MarketNewsResponse(
                query=query_text,
                age_hours=max_age_hours,
                symbol=symbol_text or None,
                symbol_name=symbol_name or None,
                source_domains=response_domains,
                items=[MarketNewsItem(**item) for item in cached_items],
                fetched_at=self._now_datetime(),
                cache_hit=True,
                fallback_used=False,
                degraded=len(cached_items) == 0,
                degraded_reason="NEWS_EMPTY" if len(cached_items) == 0 else None,
            )

        timeout = max(5.0, min(float(self._config.ai_timeout_sec), 12.0))
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            )
        }

        def fetch_eastmoney_fast_news(client: httpx.Client) -> list[dict[str, str]]:
            timestamp = int(time.time() * 1000)
            response = client.get(
                "https://np-weblist.eastmoney.com/comm/web/getFastNewsList",
                params={
                    "client": "web",
                    "biz": "web_724",
                    "fastColumn": "102",
                    "sortEnd": "",
                    "pageSize": max(50, max_items * 3),
                    "req_trace": str(timestamp),
                    "_": str(timestamp),
                },
                headers={"Accept": "application/json"},
            )
            response.raise_for_status()
            payload = response.json()

            data = payload.get("data") if isinstance(payload, dict) else None
            source_rows: list[dict[str, object]] = []
            if isinstance(data, list):
                source_rows = [item for item in data if isinstance(item, dict)]
            elif isinstance(data, dict):
                news_list = data.get("newsList")
                if isinstance(news_list, list):
                    source_rows = [item for item in news_list if isinstance(item, dict)]
                if not source_rows:
                    for value in data.values():
                        if isinstance(value, list) and value and isinstance(value[0], dict):
                            source_rows = [item for item in value if isinstance(item, dict)]
                            break

            results: list[dict[str, str]] = []
            seen_urls: set[str] = set()
            for idx, item in enumerate(source_rows):
                title = self._clean_text(html.unescape(str(item.get("title", "")).strip()))
                snippet = self._clean_text(
                    html.unescape(
                        str(
                            item.get("summary")
                            or item.get("digest")
                            or item.get("content")
                            or item.get("ltext")
                            or ""
                        ).strip()
                    )
                )
                if self._is_low_signal_title(title):
                    continue
                raw_time = str(
                    item.get("showTime")
                    or item.get("displayTime")
                    or item.get("publishTime")
                    or item.get("time")
                    or ""
                )
                parsed_dt = parse_news_datetime(raw_time)
                pub_date = to_pub_date(parsed_dt, raw_time)

                raw_unique_id = TextProcessor.clean_whitespace(
                    str(item.get("id") or item.get("newsid") or item.get("code") or "")
                )
                link = TextProcessor.clean_whitespace(str(item.get("url") or ""))
                if not link:
                    # Eastmoney fast news often has no URL and uses `code` as unique id.
                    link = f"https://kuaixun.eastmoney.com/news/{raw_unique_id}" if raw_unique_id else "https://kuaixun.eastmoney.com/"
                if link.startswith("//"):
                    link = f"https:{link}"
                elif link and not link.startswith("http"):
                    link = f"https://{link.lstrip('/')}"
                if not self._url_in_domains(link, allowed_domains):
                    continue

                source_name = self._clean_text(
                    str(item.get("source") or item.get("mediaName") or item.get("media") or item.get("infoSource") or "东方财富快讯")
                )[:40]
                corpus = f"{title} {snippet} {source_name}".lower()
                if token_filters and not any(token in corpus for token in token_filters):
                    continue
                if not title and not snippet:
                    continue

                dedupe_key = raw_unique_id or link or f"{title}:{pub_date}:{idx}"
                if dedupe_key in seen_urls:
                    continue
                seen_urls.add(dedupe_key)
                results.append(
                    {
                        "title": title[:120] if title else "无标题",
                        "url": link,
                        "snippet": (f"{source_name} | {snippet}" if source_name and snippet else (snippet or "无摘要"))[:260],
                        "pub_date": pub_date,
                        "source_name": source_name or "东方财富快讯",
                    }
                )
                if len(results) >= max_items:
                    break

            results.sort(
                key=lambda row: parse_news_datetime(str(row.get("pub_date", ""))) or datetime.min,
                reverse=True,
            )
            return results[:max_items]

        def fetch_google_rss(client: httpx.Client) -> tuple[list[dict[str, str]], str | None]:
            if symbol_text:
                query_candidates = [
                    f"{symbol_text} {symbol_name} {query_text}".strip(),
                    f"{symbol_code} {symbol_name} 新闻 公告".strip(),
                    f"{symbol_name} 板块 热点".strip(),
                    query_text,
                ]
            else:
                query_candidates = [query_text, f"{query_text} 财经", f"{query_text} A股"]

            queries: list[str] = []
            for candidate in query_candidates:
                cleaned = TextProcessor.clean_whitespace(candidate)
                if cleaned and cleaned not in queries:
                    queries.append(cleaned)

            seen_urls: set[str] = set()
            results: list[dict[str, str]] = []

            def parse_rss(xml_text: str, domain_filter: set[str]) -> list[dict[str, str]]:
                parsed_items: list[dict[str, str]] = []
                try:
                    root = ET.fromstring(xml_text)
                except ET.ParseError:
                    return parsed_items
                for item in root.findall("./channel/item"):
                    link = TextProcessor.clean_whitespace(item.findtext("link") or "")
                    source_node = item.find("source")
                    source_name = ""
                    source_url = ""
                    if source_node is not None:
                        source_name = TextProcessor.clean_whitespace(source_node.text or "")
                        source_url = TextProcessor.clean_whitespace(source_node.attrib.get("url") or "")
                    if self._is_low_quality_source(source_name, source_url):
                        continue
                    filter_url = source_url or link
                    # Prefer article link so the title can jump to the exact news page.
                    display_url = link or source_url
                    if not display_url:
                        continue
                    if display_url in seen_urls and source_url and source_url not in seen_urls:
                        display_url = source_url
                    if display_url in seen_urls:
                        continue
                    if not self._url_in_domains(filter_url, domain_filter):
                        continue
                    title_raw = html.unescape(TextProcessor.clean_whitespace(item.findtext("title") or ""))
                    desc_raw = html.unescape(TextProcessor.clean_whitespace(item.findtext("description") or ""))
                    title = self._clean_text(title_raw)
                    snippet = self._clean_text(desc_raw)
                    if self._is_low_signal_title(title):
                        continue
                    parsed_dt = parse_news_datetime(str(item.findtext("pubDate") or ""))
                    pub_date = to_pub_date(parsed_dt, str(item.findtext("pubDate") or ""))
                    corpus = f"{title} {snippet} {source_name}".lower()
                    if token_filters and not any(token in corpus for token in token_filters):
                        continue
                    if not title and not snippet:
                        continue
                    parsed_items.append(
                        {
                            "title": title[:120] if title else "无标题",
                            "url": display_url,
                            "snippet": (f"{source_name} | {snippet}" if source_name else snippet)[:260] if snippet else "无摘要",
                            "pub_date": pub_date,
                            "source_name": source_name[:40],
                        }
                    )
                    seen_urls.add(display_url)
                    if len(results) + len(parsed_items) >= max_items:
                        break
                return parsed_items

            fetch_error: str | None = None
            try:
                for item_query in queries:
                    if len(results) >= max_items:
                        break
                    response = client.get(
                        "https://news.google.com/rss/search",
                        params={
                            "q": item_query,
                            "hl": "zh-CN",
                            "gl": "CN",
                            "ceid": "CN:zh-Hans",
                        },
                    )
                    response.raise_for_status()
                    parsed_items = parse_rss(response.text, allowed_domains)
                    if not parsed_items and allowed_domains:
                        parsed_items = parse_rss(response.text, set())
                    results.extend(parsed_items)
                    if len(results) >= max_items:
                        break
            except Exception as exc:
                fetch_error = str(exc)[:160]
                results = []

            results.sort(
                key=lambda row: parse_news_datetime(str(row.get("pub_date", ""))) or datetime.min,
                reverse=True,
            )
            return results[:max_items], fetch_error

        fresh_items_raw: list[dict[str, str]] = []
        fetch_error: str | None = None
        try:
            with httpx.Client(timeout=timeout, follow_redirects=True, headers=headers) as client:
                fresh_items_raw = fetch_eastmoney_fast_news(client)
                if not fresh_items_raw:
                    fresh_items_raw, fetch_error = fetch_google_rss(client)
        except Exception as exc:
            fetch_error = str(exc)[:160]
            fresh_items_raw = []

        fresh_items = filter_recent_items(fresh_items_raw)
        if fresh_items:
            self._web_evidence_cache[cache_key] = (now_ts, fresh_items)
            self._market_news_last_success = (
                now_ts,
                fresh_items,
                {"query": query_text, "symbol": symbol_text, "source_domains": domain_key},
            )
            return MarketNewsResponse(
                query=query_text,
                age_hours=max_age_hours,
                symbol=symbol_text or None,
                symbol_name=symbol_name or None,
                source_domains=response_domains,
                items=[MarketNewsItem(**item) for item in fresh_items],
                fetched_at=self._now_datetime(),
                cache_hit=False,
                fallback_used=False,
                degraded=False,
                degraded_reason=None,
            )

        fresh_relaxed_items = filter_relaxed_items(fresh_items_raw)
        if fresh_relaxed_items:
            self._web_evidence_cache[cache_key] = (now_ts, fresh_relaxed_items)
            self._market_news_last_success = (
                now_ts,
                fresh_relaxed_items,
                {"query": query_text, "symbol": symbol_text, "source_domains": domain_key},
            )
            return MarketNewsResponse(
                query=query_text,
                age_hours=max_age_hours,
                symbol=symbol_text or None,
                symbol_name=symbol_name or None,
                source_domains=response_domains,
                items=[MarketNewsItem(**item) for item in fresh_relaxed_items],
                fetched_at=self._now_datetime(),
                cache_hit=False,
                fallback_used=True,
                degraded=True,
                degraded_reason="NEWS_OUT_OF_WINDOW",
            )

        fallback_items: list[dict[str, str]] = []
        fallback_reason: str | None = None
        if cached and cached[1]:
            fallback_items = filter_recent_items(cached[1])
            if fallback_items:
                fallback_reason = "NEWS_FALLBACK_STALE_CACHE"
            else:
                fallback_items = filter_relaxed_items(cached[1])
                if fallback_items:
                    fallback_reason = "NEWS_FALLBACK_STALE_CACHE_OUT_OF_WINDOW"
        if not fallback_items and self._market_news_last_success and self._market_news_last_success[1]:
            _, last_rows, last_meta = self._market_news_last_success
            last_symbol = TextProcessor.clean_whitespace(str(last_meta.get("symbol", ""))).lower()
            if (symbol_text and last_symbol == symbol_text) or (not symbol_text):
                fallback_items = filter_recent_items(last_rows)
                if fallback_items:
                    fallback_reason = "NEWS_FALLBACK_LAST_SUCCESS"
                else:
                    fallback_items = filter_relaxed_items(last_rows)
                    if fallback_items:
                        fallback_reason = "NEWS_FALLBACK_LAST_SUCCESS_OUT_OF_WINDOW"

        if fallback_items:
            return MarketNewsResponse(
                query=query_text,
                age_hours=max_age_hours,
                symbol=symbol_text or None,
                symbol_name=symbol_name or None,
                source_domains=response_domains,
                items=[MarketNewsItem(**item) for item in fallback_items],
                fetched_at=self._now_datetime(),
                cache_hit=False,
                fallback_used=True,
                degraded=True,
                degraded_reason=f"{fallback_reason}:{fetch_error or 'NEWS_EMPTY'}",
            )

        self._web_evidence_cache[cache_key] = (now_ts, [])
        return MarketNewsResponse(
            query=query_text,
            age_hours=max_age_hours,
            symbol=symbol_text or None,
            symbol_name=symbol_name or None,
            source_domains=response_domains,
            items=[],
            fetched_at=self._now_datetime(),
            cache_hit=False,
            fallback_used=False,
            degraded=True,
            degraded_reason=fetch_error or "NEWS_EMPTY",
        )

    def get_review_tags(self) -> ReviewTagsPayload:
        return ReviewTagsPayload(
            emotion=[item.model_copy() for item in self._review_tags.get("emotion", [])],
            reason=[item.model_copy() for item in self._review_tags.get("reason", [])],
        )

    @state_locked
    def create_review_tag(self, tag_type: ReviewTagType, payload: ReviewTagCreateRequest) -> ReviewTag:
        name = payload.name.strip()
        if not name:
            raise ValueError("tag name cannot be empty")
        for item in self._review_tags[tag_type]:
            if item.name.strip() == name:
                return item
        colors = ["blue", "cyan", "green", "gold", "orange", "red", "magenta", "purple", "geekblue", "lime"]
        color = colors[len(self._review_tags[tag_type]) % len(colors)]
        created = ReviewTag(
            id=f"{tag_type}-{uuid4().hex[:8]}",
            name=name,
            color=color,
            created_at=self._now_datetime(),
        )
        self._review_tags[tag_type].append(created)
        self._persist_app_state()
        return created

    @state_locked
    def delete_review_tag(self, tag_type: ReviewTagType, tag_id: str) -> bool:
        original_count = len(self._review_tags[tag_type])
        self._review_tags[tag_type] = [item for item in self._review_tags[tag_type] if item.id != tag_id]
        if len(self._review_tags[tag_type]) == original_count:
            return False

        updated_at = self._now_datetime()
        for order_id in list(self._fill_tag_store.keys()):
            row = self._fill_tag_store[order_id]
            next_emotion = row.emotion_tag_id
            next_reasons = list(row.reason_tag_ids)
            changed = False
            if tag_type == "emotion" and row.emotion_tag_id == tag_id:
                next_emotion = None
                changed = True
            if tag_type == "reason" and tag_id in row.reason_tag_ids:
                next_reasons = [item for item in row.reason_tag_ids if item != tag_id]
                changed = True
            if not changed:
                continue
            if not next_emotion and not next_reasons:
                self._fill_tag_store.pop(order_id, None)
                continue
            self._fill_tag_store[order_id] = TradeFillTagAssignment(
                order_id=order_id,
                emotion_tag_id=next_emotion,
                reason_tag_ids=next_reasons,
                updated_at=updated_at,
            )
        self._persist_app_state()
        return True

    def get_fill_tag_assignment(self, order_id: str) -> TradeFillTagAssignment | None:
        return self._fill_tag_store.get(order_id)

    def list_fill_tag_assignments(self) -> list[TradeFillTagAssignment]:
        rows = list(self._fill_tag_store.values())
        rows.sort(key=lambda row: row.updated_at, reverse=True)
        return rows

    def set_fill_tag_assignment(self, order_id: str, payload: TradeFillTagUpdateRequest) -> TradeFillTagAssignment:
        fill_resp = self._sim_engine.list_fills(
            symbol=None,
            side=None,
            date_from=None,
            date_to=None,
            page=1,
            page_size=200_000,
        )
        if not any(item.order_id == order_id for item in fill_resp.items):
            raise ValueError("order_id not found in fill records")

        return self._save_fill_tag_assignment(order_id, payload)

    @state_locked
    def _save_fill_tag_assignment(self, order_id: str, payload: TradeFillTagUpdateRequest) -> TradeFillTagAssignment:
        emotion_tag_id = (payload.emotion_tag_id or "").strip() or None
        if emotion_tag_id and self._find_review_tag("emotion", emotion_tag_id) is None:
            raise ValueError(f"emotion tag not found: {emotion_tag_id}")

        reason_ids = self._unique_ordered([str(item) for item in payload.reason_tag_ids])
        for item in reason_ids:
            if self._find_review_tag("reason", item) is None:
                raise ValueError(f"reason tag not found: {item}")

        if emotion_tag_id is None and not reason_ids:
            self._fill_tag_store.pop(order_id, None)
            self._persist_app_state()
            return TradeFillTagAssignment(
                order_id=order_id,
                emotion_tag_id=None,
                reason_tag_ids=[],
                updated_at=self._now_datetime(),
            )

        record = TradeFillTagAssignment(
            order_id=order_id,
            emotion_tag_id=emotion_tag_id,
            reason_tag_ids=reason_ids,
            updated_at=self._now_datetime(),
        )
        self._fill_tag_store[order_id] = record
        self._persist_app_state()
        return record

    def get_review_tag_stats(
        self,
        *,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> ReviewTagStatsResponse:
        fills = self._sim_engine.list_fills(
            symbol=None,
            side=None,
            date_from=date_from,
            date_to=date_to,
            page=1,
            page_size=200_000,
        ).items

        emotion_acc: dict[str, dict[str, float | int | str]] = {}
        reason_acc: dict[str, dict[str, float | int | str]] = {}
        for tag in self._review_tags.get("emotion", []):
            emotion_acc[tag.id] = {
                "tag_id": tag.id,
                "name": tag.name,
                "color": tag.color,
                "count": 0,
                "gross_amount": 0.0,
                "net_amount": 0.0,
            }
        for tag in self._review_tags.get("reason", []):
            reason_acc[tag.id] = {
                "tag_id": tag.id,
                "name": tag.name,
                "color": tag.color,
                "count": 0,
                "gross_amount": 0.0,
                "net_amount": 0.0,
            }

        for fill in fills:
            assignment = self._fill_tag_store.get(fill.order_id)
            if assignment is None:
                continue

            if assignment.emotion_tag_id and assignment.emotion_tag_id in emotion_acc:
                item = emotion_acc[assignment.emotion_tag_id]
                item["count"] = int(item["count"]) + 1
                item["gross_amount"] = float(item["gross_amount"]) + float(fill.gross_amount)
                item["net_amount"] = float(item["net_amount"]) + float(fill.net_amount)

            for tag_id in assignment.reason_tag_ids:
                if tag_id not in reason_acc:
                    continue
                item = reason_acc[tag_id]
                item["count"] = int(item["count"]) + 1
                item["gross_amount"] = float(item["gross_amount"]) + float(fill.gross_amount)
                item["net_amount"] = float(item["net_amount"]) + float(fill.net_amount)

        emotion_rows = [
            ReviewTagStatItem(
                tag_id=str(item["tag_id"]),
                name=str(item["name"]),
                color=str(item["color"]),
                count=int(item["count"]),
                gross_amount=float(item["gross_amount"]),
                net_amount=float(item["net_amount"]),
            )
            for item in emotion_acc.values()
            if int(item["count"]) > 0
        ]
        reason_rows = [
            ReviewTagStatItem(
                tag_id=str(item["tag_id"]),
                name=str(item["name"]),
                color=str(item["color"]),
                count=int(item["count"]),
                gross_amount=float(item["gross_amount"]),
                net_amount=float(item["net_amount"]),
            )
            for item in reason_acc.values()
            if int(item["count"]) > 0
        ]
        emotion_rows.sort(key=lambda row: (row.count, row.net_amount), reverse=True)
        reason_rows.sort(key=lambda row: (row.count, row.net_amount), reverse=True)

        return ReviewTagStatsResponse(
            date_from=date_from,
            date_to=date_to,
            emotion=emotion_rows,
            reason=reason_rows,
        )

    def _get_latest_ai_record_dict(self, symbol: str) -> dict[str, object] | None:
        normalized = symbol.strip().lower()
        for record in self._ai_record_store:
            if record.symbol == normalized:
                return record.model_dump()
        return None

    def get_ai_playbook(
        self,
        *,
        scope: str = "all",
        strategy_id: str | None = None,
    ) -> dict[str, object]:
        principles = load_local_playbook().get("principles", [])
        if not isinstance(principles, list):
            principles = []
        strategy_ids = [strategy_id] if strategy_id else None
        playbook = build_playbook(
            self._strategy_registry,
            scope=scope,  # type: ignore[arg-type]
            strategy_ids=strategy_ids,
            user_principles=[str(item) for item in principles],
        )
        text = build_playbook_text(
            self._strategy_registry,
            scope=scope,  # type: ignore[arg-type]
            strategy_ids=strategy_ids,
            user_principles=[str(item) for item in principles],
            compact=True,
        )
        return {"playbook": playbook, "text": text}

    def get_ai_local_playbook(self) -> dict[str, object]:
        return load_local_playbook()

    def set_ai_local_playbook(self, principles: list[str]) -> dict[str, object]:
        return save_local_playbook(principles)

    def get_ai_quick_prompts(self) -> dict[str, object]:
        return load_quick_prompts()

    def set_ai_quick_prompts(self, templates: list[dict[str, object]]) -> dict[str, object]:
        return save_quick_prompts(templates)

    def ai_chat(self, request: Any) -> Any:
        from .models import AIChatResponse

        try:
            return self._ai_chat_service.chat(request)
        except ValueError as exc:
            if str(exc) == "AI_CHAT_EMPTY":
                raise BacktestValidationError("AI_CHAT_EMPTY", "至少需要一条用户消息。") from exc
            raise

    def get_ai_chat_session(self, session_id: str) -> dict[str, object]:
        from .models import AIChatMessage

        messages = self._ai_chat_service.get_session(session_id)
        return {
            "session_id": session_id,
            "messages": [AIChatMessage(role=item.role, content=item.content) for item in messages],
        }

    def delete_ai_chat_session(self, session_id: str) -> bool:
        return self._ai_chat_service.delete_session(session_id)

    def ai_chat_stream(self, request: Any):
        return self._ai_chat_service.chat_stream(request)

    def list_ai_sessions(self, *, limit: int = 20) -> list[dict[str, object]]:
        return self._ai_chat_service.list_sessions(limit=limit)

    def get_ai_usage(self) -> dict[str, int]:
        return self._ai_chat_service.get_usage_totals()

    def create_ai_parameter_proposal(self, *, message: str, context: Any) -> dict[str, object]:
        return self._ai_parameter_proposal_service.create_proposal(user_message=message, context=context)

    def apply_ai_parameter_proposal(
        self,
        proposal_id: str,
        *,
        change_ids: list[str],
        confirm_high_risk: bool = False,
    ) -> dict[str, object]:
        return self._ai_parameter_proposal_service.apply_proposal(
            proposal_id,
            change_ids=change_ids,
            confirm_high_risk=confirm_high_risk,
        )

    def analyze_stock_with_ai(self, symbol: str) -> AIAnalysisRecord:
        row = self._latest_rows.get(symbol) or self._build_row_from_candles(symbol)
        if row and symbol not in self._latest_rows:
            self._latest_rows[symbol] = row
        source_urls = self._enabled_ai_source_urls()
        stock_name = self._resolve_symbol_name(symbol, row)
        board_label = self._market_board_label(symbol)
        fallback = self._heuristic_ai_analysis(symbol, row, source_urls)
        web_evidence = self._collect_web_evidence(
            symbol,
            stock_name,
            source_urls,
            focus_date=fallback.breakout_date,
        )
        evidence_urls = [item["url"] for item in web_evidence if item.get("url")]
        fallback = fallback.model_copy(update={"source_urls": evidence_urls[:8] or source_urls})
        inferred_sector = self._infer_sector_from_context(symbol, stock_name, web_evidence)
        industry_evidence = self._collect_industry_evidence(
            inferred_sector,
            source_urls,
            focus_date=fallback.breakout_date,
        )
        industry_event_candidates = self._extract_industry_event_candidates(
            inferred_sector,
            industry_evidence,
        )
        if not industry_event_candidates:
            industry_event_candidates = self._build_industry_fallback_reasons(inferred_sector, row)
        combined_evidence: list[dict[str, str]] = []
        combined_evidence.extend(web_evidence)
        combined_evidence.extend(industry_evidence)
        core_event_candidates = self._extract_core_event_candidates(web_evidence)
        hotspot_titles = self._pick_breakout_hotspot_titles(web_evidence, fallback.breakout_date, max_items=2)
        if web_evidence:
            inferred_theme = self._infer_theme_from_web_evidence(web_evidence)
            inferred_reasons = self._infer_rise_reasons_from_web_evidence(web_evidence)
            enriched_reasons = [*core_event_candidates, *inferred_reasons, *hotspot_titles, *industry_event_candidates]
            if not enriched_reasons:
                enriched_reasons = fallback.rise_reasons
            sanitized_fallback_reasons = self._sanitize_ai_rise_reasons(
                enriched_reasons,
                symbol=symbol,
                core_event_candidates=core_event_candidates,
                industry_event_candidates=industry_event_candidates,
                industry_hint=inferred_sector,
            )
            fallback = fallback.model_copy(
                update={
                    "source_urls": evidence_urls[:8] or source_urls,
                    "theme_name": self._sanitize_theme_name(
                        inferred_theme if inferred_theme != "Unknown" else (fallback.theme_name or ""),
                        evidence=combined_evidence,
                        inferred_sector=inferred_sector,
                    ),
                    "rise_reasons": sanitized_fallback_reasons[:2],
                    "summary": self._build_compact_ai_summary(
                        symbol=symbol,
                        breakout_date=fallback.breakout_date,
                        board_label=board_label,
                        inferred_sector=inferred_sector,
                        rise_reasons=sanitized_fallback_reasons,
                        raw_summary=fallback.summary,
                        row=row,
                    ),
                }
            )
        else:
            sanitized_fallback_reasons = self._sanitize_ai_rise_reasons(
                [*fallback.rise_reasons, *industry_event_candidates],
                symbol=symbol,
                core_event_candidates=core_event_candidates,
                industry_event_candidates=industry_event_candidates,
                industry_hint=inferred_sector,
            )
            fallback = fallback.model_copy(
                update={
                    "theme_name": self._sanitize_theme_name(
                        fallback.theme_name or "",
                        evidence=combined_evidence,
                        inferred_sector=inferred_sector,
                    ),
                    "rise_reasons": sanitized_fallback_reasons[:2],
                    "summary": self._build_compact_ai_summary(
                        symbol=symbol,
                        breakout_date=fallback.breakout_date,
                        board_label=board_label,
                        inferred_sector=inferred_sector,
                        rise_reasons=sanitized_fallback_reasons,
                        raw_summary=fallback.summary,
                        row=row,
                    ),
                }
            )

        provider_result = self._call_provider_for_stock(symbol, row, source_urls)
        record = provider_result or fallback
        if provider_result and provider_result.error_code:
            record = fallback.model_copy(
                update={
                    "provider": provider_result.provider,
                    "summary": provider_result.summary,
                    "error_code": provider_result.error_code,
                }
            )

        final_reasons_source = [
            *(core_event_candidates or []),
            *(industry_event_candidates or []),
            *(record.rise_reasons or []),
            *hotspot_titles,
        ]
        final_reasons = self._sanitize_ai_rise_reasons(
            final_reasons_source,
            symbol=symbol,
            core_event_candidates=core_event_candidates,
            industry_event_candidates=industry_event_candidates,
            industry_hint=inferred_sector,
        )
        final_theme = self._sanitize_theme_name(
            record.theme_name or "",
            evidence=combined_evidence,
            inferred_sector=inferred_sector,
        )
        if final_reasons and final_reasons[0].startswith("行业驱动：") and inferred_sector != "Unknown":
            final_theme = inferred_sector
        final_summary = self._build_compact_ai_summary(
            symbol=symbol,
            breakout_date=record.breakout_date,
            board_label=board_label,
            inferred_sector=inferred_sector,
            rise_reasons=final_reasons,
            raw_summary=record.summary,
            row=row,
        )
        record = record.model_copy(
            update={
                "theme_name": final_theme,
                "rise_reasons": final_reasons[:2],
                "summary": final_summary,
                "source_urls": evidence_urls[:8] or source_urls,
            }
        )

        return self._save_ai_record(record)

    @state_locked
    def _save_ai_record(self, record: AIAnalysisRecord) -> AIAnalysisRecord:
        self._ai_record_store.insert(0, record)
        if len(self._ai_record_store) > 200:
            self._ai_record_store = self._ai_record_store[:200]
        self._persist_app_state()
        return record

    def get_ai_records(self) -> list[AIAnalysisRecord]:
        return self._ai_record_store

    @state_locked
    def delete_ai_record(self, symbol: str, fetched_at: str, provider: str | None = None) -> bool:
        for index, item in enumerate(self._ai_record_store):
            if item.symbol != symbol:
                continue
            if item.fetched_at != fetched_at:
                continue
            if provider is not None and item.provider != provider:
                continue
            del self._ai_record_store[index]
            self._persist_app_state()
            return True
        return False

    @state_locked
    def get_config(self) -> AppConfig:
        return self._config.model_copy(deep=True)

    def get_public_config(self) -> AppConfig:
        config = self.get_config()
        config.api_key = "********" if config.api_key else ""
        for provider in config.ai_providers:
            provider.api_key = "********" if provider.api_key else ""
        return config

    @state_locked
    def set_config(self, payload: AppConfig) -> AppConfig:
        payload = payload.model_copy(deep=True)
        if payload.api_key == "********":
            payload.api_key = self._config.api_key
        previous = {provider.id: provider for provider in self._config.ai_providers}
        for provider in payload.ai_providers:
            if provider.api_key == "********":
                old = previous.get(provider.id)
                provider.api_key = old.api_key if old else ""
        self._config = payload
        with self._candles_lock:
            self._candles_map.clear()
            self._candles_signatures.clear()
        self._latest_rows = {}
        self._signals_cache = {}
        self._backtest_matrix_engine.clear_runtime_cache()
        self._wyckoff_event_store.clear_runtime_cache()
        self._clear_backtest_signal_matrix_runtime_cache()
        self._clear_backtest_input_pool_runtime_cache()
        self._clear_backtest_precheck_cache()
        self._persist_app_state()
        return self._config

    def get_wyckoff_event_store_stats(self) -> WyckoffEventStoreStatsResponse:
        metrics = self._snapshot_wyckoff_metrics()
        cache_hits = int(metrics.get("cache_hits", 0) or 0)
        cache_misses = int(metrics.get("cache_misses", 0) or 0)
        cache_total = cache_hits + cache_misses
        cache_hit_rate = round(cache_hits / cache_total, 6) if cache_total > 0 else 0.0
        cache_miss_rate = round(cache_misses / cache_total, 6) if cache_total > 0 else 0.0
        snapshot_reads = int(metrics.get("snapshot_reads", 0) or 0)
        snapshot_read_ms_total = float(metrics.get("snapshot_read_ms_total", 0.0) or 0.0)
        avg_snapshot_read_ms = round(snapshot_read_ms_total / snapshot_reads, 6) if snapshot_reads > 0 else 0.0
        return WyckoffEventStoreStatsResponse(
            enabled=self._wyckoff_event_store.enabled,
            read_only=self._wyckoff_event_store.read_only,
            db_path=str(self._wyckoff_event_store.db_path),
            db_exists=self._wyckoff_event_store.db_path.exists(),
            db_record_count=self._wyckoff_event_store.count_records(),
            runtime_cache_size=self._wyckoff_event_store.runtime_cache_size,
            cache_hits=cache_hits,
            cache_misses=cache_misses,
            cache_hit_rate=cache_hit_rate,
            cache_miss_rate=cache_miss_rate,
            snapshot_reads=snapshot_reads,
            avg_snapshot_read_ms=avg_snapshot_read_ms,
            lazy_fill_writes=int(metrics.get("lazy_fill_writes", 0) or 0),
            backfill_runs=int(metrics.get("backfill_runs", 0) or 0),
            backfill_writes=int(metrics.get("backfill_writes", 0) or 0),
            quality_empty_events=int(metrics.get("quality_empty_events", 0) or 0),
            quality_score_outliers=int(metrics.get("quality_score_outliers", 0) or 0),
            quality_date_misaligned=int(metrics.get("quality_date_misaligned", 0) or 0),
            last_backfill_started_at=(
                str(metrics.get("last_backfill_started_at"))
                if metrics.get("last_backfill_started_at") is not None
                else None
            ),
            last_backfill_finished_at=(
                str(metrics.get("last_backfill_finished_at"))
                if metrics.get("last_backfill_finished_at") is not None
                else None
            ),
            last_backfill_duration_sec=(
                float(metrics.get("last_backfill_duration_sec"))
                if metrics.get("last_backfill_duration_sec") is not None
                else None
            ),
            last_backfill_scan_dates=int(metrics.get("last_backfill_scan_dates", 0) or 0),
            last_backfill_symbols=int(metrics.get("last_backfill_symbols", 0) or 0),
            last_backfill_quality_empty_events=int(metrics.get("last_backfill_quality_empty_events", 0) or 0),
            last_backfill_quality_score_outliers=int(
                metrics.get("last_backfill_quality_score_outliers", 0) or 0
            ),
            last_backfill_quality_date_misaligned=int(
                metrics.get("last_backfill_quality_date_misaligned", 0) or 0
            ),
        )

    def backfill_wyckoff_event_store(
        self,
        payload: WyckoffEventStoreBackfillRequest,
    ) -> WyckoffEventStoreBackfillResponse:
        if not self._wyckoff_event_store.enabled:
            raise ValueError("威科夫事件库未启用，请先开启 TDX_TREND_WYCKOFF_STORE_ENABLED。")
        if self._wyckoff_event_store.read_only:
            raise ValueError("威科夫事件库当前为只读模式，无法执行回填。")

        scan_dates = self._build_backtest_scan_dates(payload.date_from, payload.date_to)
        if not scan_dates:
            raise ValueError("回填区间内无可扫描交易日。")

        valid_markets = [item for item in payload.markets if item in {"sh", "sz", "bj"}]
        if not valid_markets:
            valid_markets = [item for item in self._config.markets if item in {"sh", "sz", "bj"}]
        if not valid_markets:
            valid_markets = ["sh", "sz"]
        markets = list(dict.fromkeys(valid_markets))

        raw_windows = [int(item) for item in payload.window_days_list]
        window_days_list = sorted(
            list(
                dict.fromkeys(
                    item for item in raw_windows if 20 <= item <= 240
                )
            )
        )
        if not window_days_list:
            raise ValueError("window_days_list 必须至少包含一个 [20,240] 内的窗口。")

        started_at = self._now_datetime()
        started_ts = time.perf_counter()
        self._bump_wyckoff_metric("backfill_runs", 1)
        self._set_wyckoff_metric("last_backfill_started_at", started_at)

        loaded_rows_total = 0
        symbols_scanned = 0
        cache_hits = 0
        cache_misses = 0
        computed_count = 0
        write_count = 0
        quality_empty_events = 0
        quality_score_outliers = 0
        quality_date_misaligned = 0
        loader_error_counter: dict[str, int] = {}
        event_judgment_profile = self._active_event_judgment_profile()
        event_judgment_profile_hash = self._active_event_judgment_profile_hash()

        for as_of_date in scan_dates:
            input_rows, load_error = load_input_pool_from_tdx(
                tdx_root=self._config.tdx_data_path,
                markets=markets,
                return_window_days=max(5, min(120, int(self._config.return_window_days))),
                as_of_date=as_of_date,
            )
            if load_error:
                loader_error_counter[load_error] = loader_error_counter.get(load_error, 0) + 1
            loaded_rows_total += len(input_rows)

            unique_rows: list[ScreenerResult] = []
            seen_symbols: set[str] = set()
            for row in input_rows:
                symbol = str(row.symbol).strip().lower()
                if not symbol or symbol in seen_symbols:
                    continue
                seen_symbols.add(symbol)
                unique_rows.append(row)
                if len(unique_rows) >= payload.max_symbols_per_day:
                    break

            for row in unique_rows:
                symbol = str(row.symbol).strip().lower()
                if not symbol:
                    continue
                symbols_scanned += 1
                data_source = str(self._config.market_data_source).strip() or "unknown"
                for window_days in window_days_list:
                    params_hash = build_wyckoff_params_hash(
                        window_days,
                        profile_hash=event_judgment_profile_hash,
                    )
                    if not payload.force_rebuild:
                        read_started = time.perf_counter()
                        cached = self._wyckoff_event_store.get_snapshot(
                            symbol=symbol,
                            trade_date=as_of_date,
                            window_days=window_days,
                            algo_version=self._wyckoff_event_algo_version,
                            data_source=data_source,
                            data_version=self._wyckoff_event_data_version,
                            params_hash=params_hash,
                        )
                        read_duration_ms = (time.perf_counter() - read_started) * 1000.0
                        self._record_wyckoff_snapshot_read_latency(read_duration_ms)
                        if cached is not None:
                            cache_hits += 1
                            self._bump_wyckoff_metric("cache_hits", 1)
                            continue

                    cache_misses += 1
                    self._bump_wyckoff_metric("cache_misses", 1)
                    candles, resolved_as_of_date = self._slice_candles_as_of(
                        self._ensure_candles(symbol),
                        as_of_date,
                    )
                    if not candles or not resolved_as_of_date:
                        continue
                    snapshot = SignalAnalyzer.calculate_wyckoff_snapshot(
                        row,
                        candles,
                        window_days,
                        event_judgment_profile=event_judgment_profile,
                    )
                    quality_flags = self._inspect_wyckoff_snapshot_quality(
                        snapshot,
                        trade_date=resolved_as_of_date,
                    )
                    self._record_wyckoff_snapshot_quality(quality_flags)
                    quality_empty_events += int(max(0, quality_flags.get("empty_events", 0)))
                    quality_score_outliers += int(max(0, quality_flags.get("score_outliers", 0)))
                    quality_date_misaligned += int(max(0, quality_flags.get("date_misaligned", 0)))
                    computed_count += 1
                    write_ok = self._wyckoff_event_store.upsert_snapshot(
                        symbol=symbol,
                        trade_date=resolved_as_of_date,
                        window_days=window_days,
                        algo_version=self._wyckoff_event_algo_version,
                        data_source=data_source,
                        data_version=self._wyckoff_event_data_version,
                        params_hash=params_hash,
                        snapshot=snapshot,
                    )
                    if write_ok:
                        write_count += 1
                        self._bump_wyckoff_metric("backfill_writes", 1)

        finished_at = self._now_datetime()
        duration_sec = round(max(0.0, time.perf_counter() - started_ts), 4)
        self._set_wyckoff_metric("last_backfill_finished_at", finished_at)
        self._set_wyckoff_metric("last_backfill_duration_sec", duration_sec)
        self._set_wyckoff_metric("last_backfill_scan_dates", len(scan_dates))
        self._set_wyckoff_metric("last_backfill_symbols", symbols_scanned)
        self._set_wyckoff_metric("last_backfill_quality_empty_events", quality_empty_events)
        self._set_wyckoff_metric("last_backfill_quality_score_outliers", quality_score_outliers)
        self._set_wyckoff_metric("last_backfill_quality_date_misaligned", quality_date_misaligned)

        warnings: list[str] = []
        if loader_error_counter:
            for reason, count in sorted(loader_error_counter.items()):
                warnings.append(f"{reason} x{count}")
        if payload.force_rebuild:
            warnings.append("已开启 force_rebuild：命中记录也会重算并覆盖写入。")
        if quality_empty_events > 0:
            warnings.append(f"检测到空事件快照 {quality_empty_events} 条。")
        if quality_score_outliers > 0:
            warnings.append(f"检测到异常分值快照 {quality_score_outliers} 条。")
        if quality_date_misaligned > 0:
            warnings.append(f"检测到事件日期错位快照 {quality_date_misaligned} 条。")

        message = (
            f"事件库回填完成：扫描 {len(scan_dates)} 日，标的 {symbols_scanned}，"
            f"命中 {cache_hits}，重算 {computed_count}，写入 {write_count}。"
        )
        return WyckoffEventStoreBackfillResponse(
            ok=True,
            message=message,
            date_from=scan_dates[0],
            date_to=scan_dates[-1],
            markets=markets,
            window_days_list=window_days_list,
            scan_dates=len(scan_dates),
            loaded_rows_total=loaded_rows_total,
            symbols_scanned=symbols_scanned,
            cache_hits=cache_hits,
            cache_misses=cache_misses,
            computed_count=computed_count,
            write_count=write_count,
            quality_empty_events=quality_empty_events,
            quality_score_outliers=quality_score_outliers,
            quality_date_misaligned=quality_date_misaligned,
            started_at=started_at,
            finished_at=finished_at,
            duration_sec=duration_sec,
            warnings=warnings,
        )

    @staticmethod
    def _now_utc_iso() -> str:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    @classmethod
    def _validate_backtest_report_id(cls, report_id: str) -> str:
        text = str(report_id or "").strip()
        if not cls._BACKTEST_REPORT_ID_RE.fullmatch(text):
            raise BacktestValidationError("BACKTEST_REPORT_INVALID_ID", "报告ID不合法，只允许字母、数字、点、下划线和中划线。")
        return text

    @classmethod
    def _build_backtest_report_id(cls, candidate: str | None = None) -> str:
        text = str(candidate or "").strip()
        if text:
            return cls._validate_backtest_report_id(text)
        return f"bt_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}"

    @staticmethod
    def _hash_text_to_base36(text: str) -> str:
        digest = 2166136261
        for char in str(text):
            digest ^= ord(char)
            digest = (digest * 16777619) & 0xFFFFFFFF
        value = digest & 0xFFFFFFFF
        if value == 0:
            return "0"
        alphabet = "0123456789abcdefghijklmnopqrstuvwxyz"
        chars: list[str] = []
        while value > 0:
            value, remainder = divmod(value, 36)
            chars.append(alphabet[remainder])
        return "".join(reversed(chars))

    @classmethod
    def _build_imported_local_task_id(cls, report_id: str, kind: Literal["backtest", "plateau"]) -> str:
        prefix = (
            cls._IMPORTED_PLATEAU_TASK_PREFIX
            if kind == "plateau"
            else cls._IMPORTED_BACKTEST_TASK_PREFIX
        )
        raw = str(report_id or "").strip() or "report"
        safe = re.sub(r"[^0-9A-Za-z._-]", "_", raw)
        max_body_length = max(8, cls._BACKTEST_IMPORTED_TASK_ID_MAX_LENGTH - len(prefix))
        if len(safe) <= max_body_length:
            return f"{prefix}{safe}"
        hash_suffix = cls._hash_text_to_base36(raw).rjust(8, "0")[-8:]
        head_length = max(8, max_body_length - len(hash_suffix) - 1)
        return f"{prefix}{safe[:head_length]}_{hash_suffix}"

    @classmethod
    def _build_backtest_report_plateau_point_detail_file_name(cls, detail_key: str) -> str:
        normalized_detail_key = cls._validate_backtest_plateau_detail_key(detail_key)
        return (
            f"{cls._BACKTEST_REPORT_PLATEAU_POINT_DETAIL_FILE_PREFIX}"
            f"{normalized_detail_key}"
            f"{cls._BACKTEST_REPORT_PLATEAU_POINT_DETAIL_FILE_SUFFIX}"
        )

    @classmethod
    def _parse_backtest_report_plateau_point_detail_file_name(cls, path: str) -> str | None:
        normalized = str(path or "").strip().replace("\\", "/")
        prefix = cls._BACKTEST_REPORT_PLATEAU_POINT_DETAIL_FILE_PREFIX
        suffix = cls._BACKTEST_REPORT_PLATEAU_POINT_DETAIL_FILE_SUFFIX
        if not normalized.startswith(prefix) or not normalized.endswith(suffix):
            return None
        detail_key = normalized[len(prefix) : len(normalized) - len(suffix)]
        try:
            return cls._validate_backtest_plateau_detail_key(detail_key)
        except BacktestValidationError:
            return None

    @staticmethod
    def _sha256_bytes(raw: bytes) -> str:
        return hashlib.sha256(raw).hexdigest()

    @staticmethod
    def _json_dumps_bytes(payload: object) -> bytes:
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")

    def _decode_backtest_report_base64(self, text: str, *, field_name: str) -> bytes:
        raw_text = str(text or "").strip()
        if not raw_text:
            raise BacktestValidationError("BACKTEST_REPORT_INVALID", f"{field_name} 不能为空。")
        try:
            return base64.b64decode(raw_text, validate=True)
        except Exception as exc:
            raise BacktestValidationError("BACKTEST_REPORT_INVALID", f"{field_name} 不是有效Base64。") from exc

    def _build_backtest_report_manifest(
        self,
        *,
        report_id: str,
        created_at: str,
        app_name: str,
        app_version: str,
        payload_files: dict[str, bytes],
    ) -> BacktestReportManifest:
        file_rows = [
            BacktestReportManifestFile(
                path=path,
                sha256=self._sha256_bytes(content),
                bytes=len(content),
            )
            for path, content in sorted(payload_files.items())
        ]
        return BacktestReportManifest(
            schema_version=self._BACKTEST_REPORT_SCHEMA_VERSION,
            package_type=self._BACKTEST_REPORT_PACKAGE_TYPE,
            created_at=created_at,
            report_id=report_id,
            app=BacktestReportManifestApp(
                name=str(app_name or "Final Trade").strip() or "Final Trade",
                version=str(app_version or "unknown").strip() or "unknown",
            ),
            files=file_rows,
        )

    @staticmethod
    def _zip_backtest_report_files(files: dict[str, bytes]) -> bytes:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path, content in sorted(files.items()):
                archive.writestr(path, content)
        return buffer.getvalue()

    @staticmethod
    def _normalize_backtest_report_file_name(file_name: str | None, *, fallback: str) -> str:
        text = str(file_name or "").strip()
        if not text:
            return fallback
        safe_name = Path(text).name.strip()
        return safe_name or fallback

    @staticmethod
    def _resolve_backtest_report_run_request(
        run_request: BacktestRunRequest,
        run_result: BacktestResponse,
    ) -> BacktestRunRequest:
        effective = run_result.effective_run_request
        if effective is not None:
            return effective.model_copy(deep=True)
        return run_request.model_copy(deep=True)

    def _collect_backtest_report_plateau_point_details(
        self,
        *,
        plateau_result: BacktestPlateauResponse | None,
        plateau_task_id: str | None,
        explicit_details: list[BacktestPlateauPointDetailResponse],
    ) -> list[BacktestPlateauPointDetailResponse]:
        if plateau_result is None:
            return []
        allowed_detail_keys = {
            self._validate_backtest_plateau_detail_key(str(point.detail_key or "").strip())
            for point in plateau_result.points
            if str(point.detail_key or "").strip()
        }
        detail_map: dict[str, BacktestPlateauPointDetailResponse] = {}
        for detail in explicit_details:
            detail_key = self._validate_backtest_plateau_detail_key(detail.detail_key)
            if detail_key not in allowed_detail_keys:
                continue
            detail_map[detail_key] = detail.model_copy(deep=True)
        normalized_task_id = ""
        if str(plateau_task_id or "").strip():
            normalized_task_id = self._validate_backtest_task_id(str(plateau_task_id or "").strip())
        if normalized_task_id:
            for point in plateau_result.points:
                detail_key = str(point.detail_key or "").strip()
                if not detail_key:
                    continue
                normalized_detail_key = self._validate_backtest_plateau_detail_key(detail_key)
                if normalized_detail_key in detail_map:
                    continue
                detail = self.get_backtest_plateau_point_detail(normalized_task_id, normalized_detail_key)
                if detail is None:
                    continue
                detail_map[normalized_detail_key] = detail
        return [detail_map[key] for key in sorted(detail_map.keys())]

    def _build_backtest_report_plateau_point_payload_files(
        self,
        *,
        plateau_result: BacktestPlateauResponse | None,
        plateau_task_id: str | None,
        explicit_details: list[BacktestPlateauPointDetailResponse],
    ) -> dict[str, bytes]:
        payload_files: dict[str, bytes] = {}
        for detail in self._collect_backtest_report_plateau_point_details(
            plateau_result=plateau_result,
            plateau_task_id=plateau_task_id,
            explicit_details=explicit_details,
        ):
            file_name = self._build_backtest_report_plateau_point_detail_file_name(detail.detail_key)
            payload_files[file_name] = self._json_dumps_bytes(detail.model_dump(exclude_none=True))
        return payload_files

    def _parse_backtest_report_package(
        self,
        package_bytes: bytes,
    ) -> tuple[
        BacktestReportManifest,
        dict[str, bytes],
        BacktestRunRequest,
        BacktestResponse,
        BacktestPlateauResponse | None,
        list[BacktestPlateauPointDetailResponse],
    ]:
        try:
            with zipfile.ZipFile(io.BytesIO(package_bytes), mode="r") as archive:
                names = {
                    str(name).strip().replace("\\", "/")
                    for name in archive.namelist()
                    if str(name).strip() and not str(name).endswith("/")
                }
                if "manifest.json" not in names:
                    raise BacktestValidationError("BACKTEST_REPORT_INVALID", "缺少 manifest.json。")
                manifest_raw = archive.read("manifest.json")
                manifest_payload = json.loads(manifest_raw.decode("utf-8"))
                manifest = BacktestReportManifest(**manifest_payload)

                file_meta_by_path: dict[str, BacktestReportManifestFile] = {}
                for row in manifest.files:
                    normalized = str(row.path or "").strip().replace("\\", "/")
                    if (
                        not normalized
                        or normalized in {".", "..", "manifest.json"}
                        or "/" in normalized
                    ):
                        raise BacktestValidationError("BACKTEST_REPORT_INVALID", f"manifest.files.path 非法: {row.path}")
                    if normalized in file_meta_by_path:
                        raise BacktestValidationError("BACKTEST_REPORT_INVALID", f"manifest.files.path 重复: {normalized}")
                    file_meta_by_path[normalized] = row.model_copy(update={"path": normalized})

                missing_required = [
                    item
                    for item in self._BACKTEST_REPORT_REQUIRED_FILES
                    if item not in file_meta_by_path
                ]
                if missing_required:
                    raise BacktestValidationError(
                        "BACKTEST_REPORT_INVALID",
                        f"manifest 缺少必需文件: {', '.join(missing_required)}",
                    )

                for required in self._BACKTEST_REPORT_REQUIRED_FILES:
                    if required not in names:
                        raise BacktestValidationError("BACKTEST_REPORT_INVALID", f"压缩包缺少必需文件: {required}")

                extra_files = sorted(
                    name
                    for name in names
                    if name != "manifest.json" and name not in file_meta_by_path
                )
                if extra_files:
                    raise BacktestValidationError(
                        "BACKTEST_REPORT_INVALID",
                        f"压缩包存在未登记文件: {', '.join(extra_files[:8])}",
                    )

                payload_files: dict[str, bytes] = {}
                for path, meta in file_meta_by_path.items():
                    if path not in names:
                        raise BacktestValidationError("BACKTEST_REPORT_INVALID", f"压缩包缺少文件: {path}")
                    content = archive.read(path)
                    if len(content) != int(meta.bytes):
                        raise BacktestValidationError("BACKTEST_REPORT_INVALID", f"文件字节数不匹配: {path}")
                    actual_hash = self._sha256_bytes(content)
                    if actual_hash != str(meta.sha256).strip().lower():
                        raise BacktestValidationError("BACKTEST_REPORT_INVALID", f"文件哈希校验失败: {path}")
                    payload_files[path] = content
        except BacktestValidationError:
            raise
        except zipfile.BadZipFile as exc:
            raise BacktestValidationError("BACKTEST_REPORT_INVALID", "文件不是有效的ftbt压缩包。") from exc
        except Exception as exc:
            raise BacktestValidationError("BACKTEST_REPORT_INVALID", f"解析ftbt失败: {exc}") from exc

        self._validate_backtest_report_id(manifest.report_id)

        try:
            run_request = BacktestRunRequest(
                **json.loads(payload_files["run_request.json"].decode("utf-8"))
            )
            run_result = BacktestResponse(
                **json.loads(payload_files["run_result.json"].decode("utf-8"))
            )
            run_request = self._resolve_backtest_report_run_request(run_request, run_result)
        except BacktestValidationError:
            raise
        except Exception as exc:
            raise BacktestValidationError("BACKTEST_REPORT_INVALID", f"run_request/run_result 校验失败: {exc}") from exc

        plateau_result: BacktestPlateauResponse | None = None
        if "plateau_result.json" in payload_files:
            try:
                plateau_result = BacktestPlateauResponse(
                    **json.loads(payload_files["plateau_result.json"].decode("utf-8"))
                )
            except Exception as exc:
                raise BacktestValidationError("BACKTEST_REPORT_INVALID", f"plateau_result 校验失败: {exc}") from exc

        plateau_point_details: list[BacktestPlateauPointDetailResponse] = []
        for path, content in sorted(payload_files.items()):
            detail_key = self._parse_backtest_report_plateau_point_detail_file_name(path)
            if detail_key is None:
                continue
            try:
                raw = json.loads(content.decode("utf-8"))
                if not isinstance(raw, dict):
                    raise ValueError("plateau point detail payload must be an object")
                plateau_point_details.append(
                    BacktestPlateauPointDetailResponse(
                        task_id=self._validate_backtest_task_id(str(raw.get("task_id") or "pt_report_detail")),
                        detail_key=self._validate_backtest_plateau_detail_key(
                            str(raw.get("detail_key") or detail_key)
                        ),
                        saved_at=str(raw.get("saved_at") or ""),
                        params=BacktestPlateauParams(**dict(raw.get("params") or {})),
                        run_request=BacktestRunRequest(**dict(raw.get("run_request") or {})),
                        run_result=BacktestResponse(**dict(raw.get("run_result") or {})),
                    )
                )
            except BacktestValidationError:
                raise
            except Exception as exc:
                raise BacktestValidationError(
                    "BACKTEST_REPORT_INVALID",
                    f"plateau point detail 校验失败: {path}: {exc}",
                ) from exc

        return manifest, payload_files, run_request, run_result, plateau_result, plateau_point_details

    @staticmethod
    def _backtest_report_meta_path(report_dir: Path) -> Path:
        return report_dir / "meta.json"

    def _read_backtest_report_meta(self, report_dir: Path) -> dict[str, Any]:
        path = self._backtest_report_meta_path(report_dir)
        if not path.exists():
            return {}
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                return raw
            return {}
        except Exception:
            return {}

    @staticmethod
    def _build_backtest_report_summary(
        *,
        report_id: str,
        manifest: BacktestReportManifest,
        run_result: BacktestResponse,
        first_imported_at: str,
        last_imported_at: str,
        source_file_name: str,
        package_size_bytes: int,
        has_plateau_result: bool,
    ) -> BacktestReportSummary:
        return BacktestReportSummary(
            report_id=report_id,
            created_at=manifest.created_at,
            first_imported_at=first_imported_at,
            last_imported_at=last_imported_at,
            source_file_name=source_file_name,
            package_size_bytes=max(0, int(package_size_bytes)),
            trade_count=int(run_result.stats.trade_count),
            total_return=float(run_result.stats.total_return),
            max_drawdown=float(run_result.stats.max_drawdown),
            win_rate=float(run_result.stats.win_rate),
            date_from=str(run_result.range.date_from),
            date_to=str(run_result.range.date_to),
            has_plateau_result=bool(has_plateau_result),
        )

    def _load_backtest_report_detail_from_dir(self, report_dir: Path) -> BacktestReportDetail | None:
        try:
            report_id = self._validate_backtest_report_id(report_dir.name)
            manifest = BacktestReportManifest(
                **json.loads((report_dir / "manifest.json").read_text(encoding="utf-8"))
            )
            run_request = BacktestRunRequest(
                **json.loads((report_dir / "run_request.json").read_text(encoding="utf-8"))
            )
            run_result = BacktestResponse(
                **json.loads((report_dir / "run_result.json").read_text(encoding="utf-8"))
            )
            run_request = self._resolve_backtest_report_run_request(run_request, run_result)
            plateau_result: BacktestPlateauResponse | None = None
            plateau_path = report_dir / "plateau_result.json"
            if plateau_path.exists():
                plateau_result = BacktestPlateauResponse(
                    **json.loads(plateau_path.read_text(encoding="utf-8"))
                )
            meta = self._read_backtest_report_meta(report_dir)
            first_imported_at = str(meta.get("first_imported_at") or manifest.created_at)
            last_imported_at = str(meta.get("last_imported_at") or first_imported_at)
            source_file_name = str(meta.get("source_file_name") or f"{report_id}.ftbt")
            package_size_bytes = int(meta.get("package_size_bytes") or 0)
            summary = self._build_backtest_report_summary(
                report_id=report_id,
                manifest=manifest,
                run_result=run_result,
                first_imported_at=first_imported_at,
                last_imported_at=last_imported_at,
                source_file_name=source_file_name,
                package_size_bytes=package_size_bytes,
                has_plateau_result=plateau_result is not None,
            )
            return BacktestReportDetail(
                summary=summary,
                manifest=manifest,
                run_request=run_request,
                run_result=run_result,
                plateau_result=plateau_result,
            )
        except Exception:
            return None

    def build_backtest_report_package(self, payload: BacktestReportBuildRequest) -> BacktestReportBuildResponse:
        report_id = self._build_backtest_report_id(payload.report_id)
        created_at = self._now_utc_iso()
        report_html_text = str(payload.report_html or "")
        if not report_html_text.strip():
            raise BacktestValidationError("BACKTEST_REPORT_INVALID", "report_html 不能为空。")
        report_xlsx_bytes = self._decode_backtest_report_base64(
            payload.report_xlsx_base64,
            field_name="report_xlsx_base64",
        )
        if len(report_xlsx_bytes) <= 0:
            raise BacktestValidationError("BACKTEST_REPORT_INVALID", "report_xlsx_base64 解码后为空。")

        effective_run_request = self._resolve_backtest_report_run_request(
            payload.run_request,
            payload.run_result,
        )

        payload_files: dict[str, bytes] = {
            "run_request.json": self._json_dumps_bytes(effective_run_request.model_dump(exclude_none=True)),
            "run_result.json": self._json_dumps_bytes(payload.run_result.model_dump(exclude_none=True)),
            "report.xlsx": report_xlsx_bytes,
            "report.html": report_html_text.encode("utf-8"),
        }
        if payload.plateau_result is not None:
            payload_files["plateau_result.json"] = self._json_dumps_bytes(
                payload.plateau_result.model_dump(exclude_none=True)
            )
        payload_files.update(
            self._build_backtest_report_plateau_point_payload_files(
                plateau_result=payload.plateau_result,
                plateau_task_id=payload.plateau_task_id,
                explicit_details=list(payload.plateau_point_details or []),
            )
        )

        manifest = self._build_backtest_report_manifest(
            report_id=report_id,
            created_at=created_at,
            app_name=payload.app_name,
            app_version=payload.app_version,
            payload_files=payload_files,
        )

        package_files: dict[str, bytes] = {
            "manifest.json": self._json_dumps_bytes(manifest.model_dump(exclude_none=True)),
            **payload_files,
        }
        package_bytes = self._zip_backtest_report_files(package_files)
        package_base64 = base64.b64encode(package_bytes).decode("ascii")

        return BacktestReportBuildResponse(
            report_id=report_id,
            file_name=f"{report_id}.ftbt",
            file_base64=package_base64,
            manifest=manifest,
        )

    def import_backtest_report_package(
        self,
        package_bytes: bytes,
        *,
        source_file_name: str | None = None,
    ) -> BacktestReportImportResponse:
        if len(package_bytes) <= 0:
            raise BacktestValidationError("BACKTEST_REPORT_INVALID", "导入文件为空。")

        (
            manifest,
            payload_files,
            run_request,
            run_result,
            plateau_result,
            plateau_point_details,
        ) = self._parse_backtest_report_package(package_bytes)
        _ = run_request
        report_id = self._validate_backtest_report_id(manifest.report_id)
        imported_plateau_task_id = self._build_imported_local_task_id(report_id, "plateau")
        now_text = self._now_datetime()
        store_dir = self._resolve_backtest_report_store_dir()
        store_dir.mkdir(parents=True, exist_ok=True)
        target_dir = store_dir / report_id
        existing_meta = self._read_backtest_report_meta(target_dir) if target_dir.exists() else {}
        first_imported_at = str(existing_meta.get("first_imported_at") or now_text)
        normalized_source_name = self._normalize_backtest_report_file_name(
            source_file_name,
            fallback=f"{report_id}.ftbt",
        )
        package_size_bytes = len(package_bytes)

        tmp_dir = store_dir / f".{report_id}.tmp.{uuid4().hex[:8]}"
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir, ignore_errors=True)
        tmp_dir.mkdir(parents=True, exist_ok=True)
        self._clear_backtest_plateau_task_detail_store(imported_plateau_task_id)

        manifest_bytes = self._json_dumps_bytes(manifest.model_dump(exclude_none=True))
        run_request_bytes = self._json_dumps_bytes(run_request.model_dump(exclude_none=True))
        run_result_bytes = self._json_dumps_bytes(run_result.model_dump(exclude_none=True))
        meta_payload = {
            "schema_version": self._BACKTEST_REPORT_META_SCHEMA_VERSION,
            "report_id": report_id,
            "first_imported_at": first_imported_at,
            "last_imported_at": now_text,
            "source_file_name": normalized_source_name,
            "package_size_bytes": package_size_bytes,
            "updated_at": now_text,
        }
        try:
            (tmp_dir / "manifest.json").write_bytes(manifest_bytes)
            (tmp_dir / "run_request.json").write_bytes(run_request_bytes)
            (tmp_dir / "run_result.json").write_bytes(run_result_bytes)
            for path, content in payload_files.items():
                (tmp_dir / path).write_bytes(content)
            for detail in plateau_point_details:
                self._persist_backtest_plateau_point_detail(
                    task_id=imported_plateau_task_id,
                    detail_key=detail.detail_key,
                    params=detail.params,
                    run_request=detail.run_request,
                    run_result=detail.run_result,
                )
            (tmp_dir / "meta.json").write_text(
                json.dumps(meta_payload, ensure_ascii=False, sort_keys=True, indent=2),
                encoding="utf-8",
            )
            if target_dir.exists():
                shutil.rmtree(target_dir, ignore_errors=True)
            tmp_dir.replace(target_dir)
        except Exception as exc:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            raise BacktestValidationError("BACKTEST_REPORT_IMPORT_FAILED", f"写入报告库失败: {exc}") from exc

        summary = self._build_backtest_report_summary(
            report_id=report_id,
            manifest=manifest,
            run_result=run_result,
            first_imported_at=first_imported_at,
            last_imported_at=now_text,
            source_file_name=normalized_source_name,
            package_size_bytes=package_size_bytes,
            has_plateau_result=plateau_result is not None,
        )
        return BacktestReportImportResponse(summary=summary)

    def list_backtest_reports(self) -> BacktestReportListResponse:
        store_dir = self._resolve_backtest_report_store_dir()
        if not store_dir.exists() or (not store_dir.is_dir()):
            return BacktestReportListResponse(items=[])
        items: list[BacktestReportSummary] = []
        for path in store_dir.iterdir():
            if not path.is_dir():
                continue
            detail = self._load_backtest_report_detail_from_dir(path)
            if detail is None:
                continue
            items.append(detail.summary)
        items.sort(
            key=lambda row: (
                str(row.last_imported_at),
                str(row.created_at),
                str(row.report_id),
            ),
            reverse=True,
        )
        return BacktestReportListResponse(items=items)

    def get_backtest_report(self, report_id: str) -> BacktestReportDetail | None:
        normalized = self._validate_backtest_report_id(report_id)
        report_dir = self._resolve_backtest_report_store_dir() / normalized
        if not report_dir.exists() or (not report_dir.is_dir()):
            return None
        return self._load_backtest_report_detail_from_dir(report_dir)

    def delete_backtest_report(self, report_id: str) -> bool:
        normalized = self._validate_backtest_report_id(report_id)
        report_dir = self._resolve_backtest_report_store_dir() / normalized
        if not report_dir.exists() or (not report_dir.is_dir()):
            return False
        imported_plateau_task_id = self._build_imported_local_task_id(normalized, "plateau")
        try:
            shutil.rmtree(report_dir)
            self._clear_backtest_plateau_task_detail_store(imported_plateau_task_id)
            return True
        except Exception as exc:
            raise BacktestValidationError("BACKTEST_REPORT_DELETE_FAILED", f"删除报告失败: {exc}") from exc

    def get_system_storage_status(self) -> SystemStorageStatus:
        configured = (self._config.akshare_cache_dir or "").strip()
        if configured:
            resolved_cache_dir = self._resolve_user_path(configured)
        else:
            resolved_cache_dir = Path.home() / ".tdx-trend" / "akshare" / "daily"
        cache_exists = resolved_cache_dir.exists() and resolved_cache_dir.is_dir()
        cache_file_count = len(list(resolved_cache_dir.glob("*.csv"))) if cache_exists else 0

        candidates: set[str] = set()
        candidates.add(str(resolved_cache_dir))
        default_cache_dir = Path.home() / ".tdx-trend" / "akshare" / "daily"
        candidates.add(str(default_cache_dir))
        env_cache_dir = os.getenv("AKSHARE_CACHE_DIR", "").strip()
        if env_cache_dir:
            candidates.add(str(self._resolve_user_path(env_cache_dir)))
        home_cache_root = Path.home() / ".tdx-trend" / "akshare"
        if home_cache_root.exists() and home_cache_root.is_dir():
            for path in home_cache_root.glob("**"):
                if not path.is_dir():
                    continue
                if any(path.glob("*.csv")):
                    candidates.add(str(path))
        ordered_candidates = sorted(path for path in candidates if path.strip())

        sim_state_env = os.getenv("TDX_TREND_SIM_STATE_PATH", "").strip()
        sim_state_path = self._resolve_user_path(sim_state_env) if sim_state_env else Path.home() / ".tdx-trend" / "sim_state.json"
        return SystemStorageStatus(
            app_state_path=str(self._app_state_path),
            app_state_exists=self._app_state_path.exists(),
            sim_state_path=str(sim_state_path),
            sim_state_exists=sim_state_path.exists(),
            akshare_cache_dir=configured,
            akshare_cache_dir_resolved=str(resolved_cache_dir),
            akshare_cache_dir_exists=cache_exists,
            akshare_cache_file_count=cache_file_count,
            akshare_cache_candidates=ordered_candidates,
            wyckoff_event_store_path=str(self._wyckoff_event_store.db_path),
            wyckoff_event_store_exists=self._wyckoff_event_store.db_path.exists(),
            wyckoff_event_store_read_only=self._wyckoff_event_store.read_only,
        )

    def sync_market_data(self, payload: MarketDataSyncRequest) -> MarketDataSyncResponse:
        out_dir = (payload.out_dir or "").strip() or (self._config.akshare_cache_dir or "").strip()
        if not out_dir:
            out_dir = str(Path.home() / ".tdx-trend" / "akshare" / "daily")

        started = self._now_datetime()
        try:
            summary = sync_baostock_daily(
                symbols_text=payload.symbols,
                all_market=payload.all_market,
                limit=payload.limit,
                mode=payload.mode,
                start_date=payload.start_date,
                end_date=payload.end_date,
                initial_days=payload.initial_days,
                sleep_sec=payload.sleep_sec,
                out_dir=out_dir,
            )
            if int(summary.get("ok_count", 0)) > 0:
                # Ensure subsequent APIs reload latest local files after sync.
                with self._candles_lock:
                    self._candles_map.clear()
                    self._candles_signatures.clear()
                self._latest_rows = {}
                self._signals_cache = {}
                self._backtest_matrix_engine.clear_runtime_cache()
                self._wyckoff_event_store.clear_runtime_cache()
                self._clear_backtest_signal_matrix_runtime_cache()
                self._clear_backtest_input_pool_runtime_cache()
                self._clear_backtest_precheck_cache()
            errors = [str(item) for item in summary.get("errors", []) if str(item).strip()]
            failed = int(summary.get("fail_count", 0))
            ok = failed == 0
            message = (
                f"Baostock 同步完成: 成功 {summary.get('ok_count', 0)} / "
                f"失败 {failed} / 跳过 {summary.get('skipped_count', 0)} / "
                f"新增 {summary.get('new_rows_total', 0)} 行"
            )
            if not ok and errors:
                message = f"{message}（首个错误: {errors[0]}）"
            return MarketDataSyncResponse(
                ok=ok,
                provider="baostock",
                mode="full" if payload.mode == "full" else "incremental",
                message=message,
                out_dir=str(summary.get("out_dir", out_dir)),
                symbol_count=int(summary.get("symbol_count", 0)),
                ok_count=int(summary.get("ok_count", 0)),
                fail_count=failed,
                skipped_count=int(summary.get("skipped_count", 0)),
                new_rows_total=int(summary.get("new_rows_total", 0)),
                started_at=str(summary.get("started_at", started)),
                finished_at=str(summary.get("finished_at", self._now_datetime())),
                duration_sec=float(summary.get("duration_sec", 0.0)),
                errors=errors[:50],
            )
        except Exception as exc:
            return MarketDataSyncResponse(
                ok=False,
                provider="baostock",
                mode="full" if payload.mode == "full" else "incremental",
                message=f"Baostock 同步失败: {type(exc).__name__}: {exc}",
                out_dir=out_dir,
                started_at=started,
                finished_at=self._now_datetime(),
                duration_sec=0.0,
                errors=[str(exc)],
            )


store = InMemoryStore()
