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
    os.environ["AKSHARE_CACHE_DIR"] = str(directory / "online-cache")
    return directory


def load_reference(directory, source=None, code_root=None):
    directory = configure_environment(directory)
    sys.path.insert(0, str(Path(code_root) if code_root else REFERENCE / "backend"))
    from app.store import store
    from app.core import ai_session_store
    from app.core import ai_local_store
    ai_session_store.DEFAULT_STATE_DIR = directory
    ai_local_store.DEFAULT_STATE_DIR = directory
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
        readonly = request.url.path.startswith('/api/atrade/') and not request.url.path.startswith('/api/atrade/stocks/') or request.url.path.startswith('/api/backtest/reports') or request.method == 'GET' and (re.fullmatch(r'/api/backtest/(?:plateau/)?tasks(?:/[^/]+)?', request.url.path) or request.url.path in {'/api/config', '/api/strategies'})
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
