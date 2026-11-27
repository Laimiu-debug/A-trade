"""Host the pinned strategy API in an isolated A-trade workspace."""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
import secrets
import socket
import sqlite3
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
REFERENCE = ROOT / "vendor" / "finaltrade"


from service.dev_dependencies import use_prepared_dependencies

use_prepared_dependencies("fastapi", "uvicorn")


def configure_environment(directory):
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    locations = {
        "APP_STATE_PATH": "app-state.json", "SIM_STATE_PATH": "sim-state.json",
        "WYCKOFF_STORE_PATH": "wyckoff.sqlite3", "BACKTEST_TASK_STATE_PATH": "backtest-tasks.json",
        "BACKTEST_PLATEAU_TASK_STATE_PATH": "plateau-tasks.json",
        "BACKTEST_PLATEAU_DETAIL_STORE_DIR": "plateau-details",
        "BACKTEST_INPUT_POOL_CACHE_DIR": "input-cache", "SCREENER_RESULT_CACHE_DIR": "screener-cache",
        "SIGNALS_CACHE_DIR": "signal-cache", "BACKTEST_TREND_FILTER_CACHE_DIR": "trend-cache",
        "BACKTEST_RESULT_CACHE_DIR": "backtest-cache", "BACKTEST_SIGNAL_MATRIX_CACHE_DIR": "matrix-cache",
        "BACKTEST_MATRIX_CACHE_DIR": "matrix-engine-cache",
        "BACKTEST_REPORT_STORE_DIR": "reports", "CROSS_VALIDATE_HISTORY_DIR": "cross-validate",
    }
    for key, name in locations.items():
        os.environ["TDX_TREND_" + key] = str(directory / name)
    os.environ["TDX_TREND_STATE_DIR"] = str(directory)
    os.environ["AKSHARE_CACHE_DIR"] = str(directory / "online-cache")
    return directory


def load_reference(directory, source=None, code_root=None):
    directory = configure_environment(directory)
    sys.path.insert(0, str(Path(code_root) if code_root else REFERENCE / "backend"))
    from app.core import ai_session_store
    from app.core import ai_local_store
    ai_session_store.DEFAULT_STATE_DIR = directory
    ai_local_store.DEFAULT_STATE_DIR = directory
    from app.store import store
    store._resolve_trend_leaders_cache_path = lambda: directory / "trend-leaders-cache.json"
    config = store.get_config()
    config.market_data_source = "tdx_only"
    config.akshare_cache_dir = str(directory / "online-cache")
    # A source is chosen by A-trade, never taken from reference defaults or keys.
    source_path = Path(source) if source else directory / "no-market-source"
    if (source_path / "vipdoc").is_dir():
        source_path = source_path / "vipdoc"
    config.tdx_data_path = str(source_path)
    config.api_key = ""
    config.api_key_path = str(directory / "unused.key")
    config.ai_providers = []
    config.ai_sources = []
    store.set_config(config)
    return store


def configured_source(workspace):
    database = Path(workspace) / "market.sqlite3"
    if not database.exists():
        return None
    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as db:
        row = db.execute("SELECT value FROM meta WHERE key='path'").fetchone()
        return json.loads(row[0]) if row else None


def source_for_pending_tasks(workspace, selected):
    """Keep interrupted tasks on their saved directory across desktop restarts."""
    directory = Path(workspace) / 'finaltrade'
    for name in ('backtest-tasks.json', 'plateau-tasks.json'):
        try:
            state = json.loads((directory / name).read_text(encoding='utf-8'))
            active = any(row.get('task', {}).get('status') in {'pending', 'running', 'paused'} for row in state.get('tasks', []))
        except (OSError, ValueError, AttributeError):
            active = False
        if active:
            try:
                saved = json.loads((directory / 'market-source.json').read_text(encoding='utf-8'))
                return saved['source']
            except (OSError, ValueError, KeyError):
                # Migrate existing workspaces using the service's last saved config.
                try:
                    config = json.loads((directory / 'app-state.json').read_text(encoding='utf-8'))
                    prior = config['config']['tdx_data_path']
                    if selected and Path(prior).resolve() in {Path(selected).resolve(), (Path(selected) / 'vipdoc').resolve()}:
                        return selected
                    return prior
                except (OSError, ValueError, KeyError):
                    return selected
    return selected


def verify_source():
    manifest = json.loads((REFERENCE / "source-manifest.json").read_text(encoding="utf-8"))
    for entry in manifest["files"]:
        data = (REFERENCE / entry["path"]).read_bytes()
        if hashlib.sha256(data).hexdigest() != entry["sha256"]:
            raise RuntimeError("策略源文件与固定版本不一致：" + entry["path"])
    return manifest


def require_real_market_input(store):
    """Keep the source algorithm intact while disabling its no-data demo branch."""
    from app.store import MarketDataUnavailable
    original = store._load_input_pool_rows

    def load_input_pool_rows(**params):
        rows, error, cached = original(**params)
        if not rows:
            raise MarketDataUnavailable("所选市场和日期没有可用的通达信日线，请先在行情设置中选择数据目录。" + (" " + error if error else ""))
        return rows, error, cached

    store._load_input_pool_rows = load_input_pool_rows


def create_application(workspace, token, source=None):
    from fastapi.responses import FileResponse, JSONResponse
    verify_source()
    workspace = Path(workspace).resolve()
    selected_source = source_for_pending_tasks(workspace, source or configured_source(workspace))
    store = load_reference(workspace / "finaltrade", selected_source)
    require_real_market_input(store)
    from app.main import app
    from service.strategy_integration import StrategyIntegration
    from fastapi import Body
    integration = StrategyIntegration(workspace, store, selected_source)
    static = ROOT / "preview" / "finaltrade"

    @app.middleware("http")
    async def private_desktop_api(request, call_next):
        supplied = request.headers.get("x-atrade-reference-token", "")
        if not secrets.compare_digest(supplied.encode(), token.encode()):
            return JSONResponse({"code": "DESKTOP_ONLY", "message": "请从 A-trade 桌面打开。"}, status_code=403)
        if request.url.path == '/api/config' and request.method != 'GET':
            return JSONResponse({'code': 'PROJECT_SETTINGS_ONLY', 'message': '请在 A-trade 的设置中修改数据源与参数。'}, status_code=403)
        configured = source if source is not None else configured_source(workspace)
        readonly = request.url.path.startswith('/api/atrade/') and not request.url.path.startswith('/api/atrade/stocks/') and not request.url.path.startswith('/api/atrade/master-plan') or request.url.path.startswith('/api/backtest/reports') or request.method == 'GET' and (re.fullmatch(r'/api/backtest/(?:plateau/)?tasks(?:/[^/]+)?', request.url.path) or request.url.path in {'/api/config', '/api/strategies'})
        control = request.method == 'POST' and re.fullmatch(r'/api/backtest/(?:plateau/)?tasks/[^/]+/(pause|resume|cancel)', request.url.path)
        if request.url.path.startswith('/api/') and configured != integration.source:
            if integration.active():
                if not readonly and not control:
                    return JSONResponse({'code': 'SOURCE_IN_USE', 'message': '当前任务仍在使用此前的行情目录。请先完成或停止这些任务，再运行新目录的数据。'}, status_code=409)
            else:
                with integration.lock:
                    config = store.get_config()
                    source_path = Path(configured) if configured else workspace / 'finaltrade' / 'no-market-source'
                    if (source_path / 'vipdoc').is_dir():
                        source_path = source_path / 'vipdoc'
                    config.tdx_data_path = str(source_path)
                    store.set_config(config)
                    integration.source = configured
                    integration.save_source()
        tracking = request.url.path.startswith('/api/') and not readonly and not control
        if tracking:
            with integration.lock:
                integration.requests += 1
        try:
            return await call_next(request)
        finally:
            if tracking:
                with integration.lock:
                    integration.requests -= 1

    @app.get('/api/atrade/status')
    def project_status():
        return integration.status(source if source is not None else configured_source(workspace))

    @app.get('/api/atrade/tasks')
    def project_tasks():
        return integration.tasks()

    @app.get('/api/atrade/stocks/{symbol}')
    def project_stock_research(symbol: str):
        from service.stock_strategy import stock_research
        try:
            return stock_research(integration, symbol)
        except ValueError as error:
            return JSONResponse({'code': 'STOCK_RESEARCH_UNAVAILABLE', 'message': str(error)}, status_code=400)

    @app.put('/api/atrade/stocks/{symbol}/annotation')
    def project_stock_annotation(symbol: str, body: dict = Body(...)):
        from service.stock_strategy import stock_symbol
        from app.models import StockAnnotation
        try:
            symbol = stock_symbol(symbol)
            if body.get('symbol') != symbol:
                raise ValueError('标注与当前证券不一致。')
            if len(str(body.get('notes', ''))) > 6000:
                raise ValueError('研究笔记最多 6000 字。')
            annotation = StockAnnotation.model_validate(body)
            import datetime as dt
            dt.date.fromisoformat(annotation.start_date)
            saved = store.save_annotation(annotation)
            return {'annotation': saved.model_dump(mode='json')}
        except ValueError as error:
            return JSONResponse({'code': 'STOCK_ANNOTATION_INVALID', 'message': str(error)}, status_code=400)

    @app.patch('/api/atrade/tasks/{task_id}')
    def rename_project_task(task_id: str, body: dict = Body(...)):
        try:
            return integration.rename(task_id, body.get('name'))
        except ValueError as error:
            return JSONResponse({'code': 'TASK_NAME_INVALID', 'message': str(error)}, status_code=400)

    @app.post('/api/atrade/master-plan')
    def project_master_plan(body: dict = Body(default={})):
        from app.core.master_alpha_strategies import MASTER_STYLE_EXECUTION_DEFAULTS, MASTER_TRADER_STYLE_MAP
        from service.account_service import AccountService
        from service.master_advisor import generate_master_advice

        strategy_id = str(body.get('strategy_id') or 'wujiang_mainline_v1').strip()
        style_key = MASTER_TRADER_STYLE_MAP.get(strategy_id)
        if not style_key:
            return JSONResponse({'code': 'NOT_MASTER_STRATEGY', 'message': '当前策略不是大佬实战模式策略。'}, status_code=400)

        strategy_params = body.get('strategy_params') if isinstance(body.get('strategy_params'), dict) else {}
        as_of_date = str(body.get('as_of_date') or '').strip() or None
        mode = str(body.get('mode') or 'full_market').strip()
        if mode not in ('full_market', 'trend_pool'):
            mode = 'full_market'
        run_id = str(body.get('run_id') or '').strip() or None
        trend_step = str(body.get('trend_step') or 'auto').strip() or 'auto'
        window_days = max(20, min(240, int(body.get('window_days') or 60)))
        min_score = max(0.0, min(100.0, float(body.get('min_score') if body.get('min_score') is not None else 55.0)))

        # Reuse pre-computed signals from SignalsPage when provided; otherwise query store.get_signals
        external_candidates = None
        pool_label = ''
        signals_as_of = as_of_date
        provided_candidates = body.get('provided_candidates')
        if isinstance(provided_candidates, list):
            signals_as_of = str(body.get('signals_as_of') or as_of_date or '').strip() or None
            source_count = int(body.get('source_count') or len(provided_candidates))
            external_candidates = [
                {
                    'symbol': str(it.get('symbol') or ''),
                    'name': str(it.get('name') or ''),
                    'score': float(it.get('score') if it.get('score') is not None else 70.0),
                    'signalLabel': str(it.get('signalLabel') or ''),
                    'reason': str(it.get('reason') or ''),
                }
                for it in provided_candidates[:20]
                if isinstance(it, dict) and str(it.get('symbol') or '').strip()
            ]
            pool_label = f"策略运行同源扫描（{'全市场' if mode == 'full_market' else '趋势池'} · 候选 {source_count} 只 → 触发 {len(external_candidates)} 只）"
        else:
            try:
                try:
                    sig_resp = store.get_signals(
                        mode=mode,
                        run_id=run_id,
                        trend_step=trend_step,
                        strategy_id=strategy_id,
                        strategy_params=strategy_params,
                        as_of_date=as_of_date,
                        refresh=bool(body.get('refresh', False)),
                        window_days=window_days,
                        min_score=min_score,
                    )
                except Exception:
                    if mode == 'trend_pool':
                        sig_resp = store.get_signals(
                            mode='full_market',
                            strategy_id=strategy_id,
                            strategy_params=strategy_params,
                            as_of_date=as_of_date,
                            refresh=bool(body.get('refresh', False)),
                            window_days=window_days,
                            min_score=min_score,
                        )
                        mode = 'full_market'
                    else:
                        raise
                signals_as_of = sig_resp.as_of_date or as_of_date
                active_items = [it for it in sig_resp.items if int(getattr(it, 'signal_age_days', 0) or 0) <= 2]
                if not active_items and sig_resp.items:
                    active_items = list(sig_resp.items[:16])
                external_candidates = [
                    {
                        'symbol': it.symbol,
                        'name': it.name,
                        'score': float(it.entry_quality_score or 70.0),
                        'signalLabel': it.wyckoff_signal or it.primary_signal,
                        'reason': it.phase_hint or it.trigger_reason,
                    }
                    for it in active_items[:20]
                ]
                pool_label = f"策略运行同源扫描（{'全市场' if mode == 'full_market' else '趋势池'} · 候选 {sig_resp.source_count} 只 → 触发 {len(external_candidates)} 只）"
            except Exception:
                external_candidates = None

        # Read real account positions or custom rehearsal holdings
        use_custom = bool(body.get('use_custom_holdings', False))
        positions = []
        account_summary = {}
        account_source = 'account'
        account_name = ''
        if use_custom:
            account_source = 'custom'
            raw_pos = body.get('custom_positions') if isinstance(body.get('custom_positions'), list) else []
            positions = [
                {
                    'code': str(p.get('code') or ''),
                    'name': str(p.get('name') or p.get('code') or ''),
                    'quantity': float(p.get('quantity') or 0),
                    'averageCost': float(p.get('averageCost') or 0),
                }
                for p in raw_pos
                if p.get('code') and float(p.get('quantity') or 0) > 0
            ]
            eq_val = float(body.get('custom_equity') or integration.preferences().get('initialCapital') or 100000.0)
            account_summary = {'equity': eq_val}
            if body.get('custom_cash') not in (None, ''):
                account_summary['cash'] = float(body.get('custom_cash'))
        else:
            try:
                acc_svc = AccountService(workspace)
                acc_id = body.get('account_id')
                ws_data = acc_svc.dispatch('workspace', {'accountId': acc_id} if acc_id else {})
                positions = list(ws_data.get('positions') or [])
                account_summary = dict(ws_data.get('summary') or {})
                account_name = (ws_data.get('account') or {}).get('name') or ''
            except Exception:
                positions = []
                account_summary = {'equity': float(integration.preferences().get('initialCapital') or 100000.0)}

        defaults_cfg = MASTER_STYLE_EXECUTION_DEFAULTS[style_key]
        raw_max_pos = body.get('max_positions')
        has_custom_slots = raw_max_pos not in (None, '', 0, '0')
        raw_stop = body.get('stop_loss_pct')
        stop_pct = float(raw_stop) if raw_stop not in (None, '') else -abs(float(defaults_cfg['stop_loss']) * 100.0)
        if stop_pct > 0:
            stop_pct = -stop_pct

        overrides = {
            'maxPositions': int(raw_max_pos) if has_custom_slots else int(defaults_cfg['default_slots']),
            'customMaxPositions': bool(has_custom_slots),
            'stopLossPct': stop_pct,
            'useRegimeSwitch': bool(strategy_params.get('use_regime_switch', True)),
            'useFastWeakCut': bool(strategy_params.get('use_fast_weak_cut', True)),
            'useAddOnWin': bool(strategy_params.get('use_add_on_win', defaults_cfg['use_add_on_win'])),
            'usePartialTrim': bool(strategy_params.get('use_partial_trim', True)),
            'accountSource': account_source,
            'accountName': account_name,
        }

        cfg = store.get_config()
        tdx_source = cfg.tdx_data_path if (cfg.tdx_data_path and 'no-market-source' not in str(cfg.tdx_data_path)) else (source or configured_source(workspace))
        advice = generate_master_advice(
            None,
            None,
            style_key=style_key,
            target_date=signals_as_of,
            positions=positions,
            account_summary=account_summary,
            overrides=overrides,
            tdx_root_override=tdx_source,
            external_candidates=external_candidates,
            external_pool_label=pool_label,
        )
        advice['strategyId'] = strategy_id
        return advice

    @app.get("/{file_path:path}")
    def frontend(file_path: str):
        if file_path.startswith("api/"):
            return JSONResponse({"code": "NOT_FOUND", "message": "接口不存在。"}, status_code=404)
        candidate = (static / file_path).resolve()
        if not candidate.is_relative_to(static.resolve()):
            return JSONResponse({}, status_code=403)
        if not candidate.is_file():
            candidate = static / "index.html"
        return FileResponse(candidate, headers={
            "Cache-Control": "no-cache",
            "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self'; frame-ancestors atrade://app; object-src 'none'",
        })
    return app


def main():
    import uvicorn
    workspace = Path(sys.argv[1])
    token = sys.stdin.readline().strip()
    if len(token) < 32:
        raise RuntimeError("Missing desktop handshake")
    app = create_application(workspace, token)
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    print(json.dumps({"ready": True, "port": listener.getsockname()[1]}), flush=True)
    uvicorn.Server(uvicorn.Config(app, access_log=False, log_level="warning")).run(sockets=[listener])


if __name__ == "__main__":
    main()
