"""Durable research queue. Immutable local inputs; two bounded background workers.

SQLite commits checkpoint + progress together. A killed process can only lose the
last uncommitted chunk; resumption repeats that chunk without duplicate fills.
"""
from __future__ import annotations

from contextlib import contextmanager
import copy
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import threading
import tempfile
import uuid
import zlib

from service import tdx
from service.backtest_engine import BacktestEngine, normalize_config
from service.strategies import catalogue
from service.plateau import PlateauRunner
from service import portfolio_config

MAX_SYMBOLS = 50
MAX_BARS = 150000
MAX_WORKERS = 2
ACTIVE = {"queued", "running", "pausing", "stopping"}
PORTFOLIO_KINDS = {"portfolio", "comparison", "walkForward", "systemPlateau", "poolPreview",
                   "singleBacktest", "singlePlateau", "frozenValidation"}


def _now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds")


def _dump(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True)


def _pack(value):
    # Avoid simultaneously allocating a full-market JSON string and its UTF-8
    # copy. Buffer small encoder fragments before passing them to zlib.
    encoder = json.JSONEncoder(ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True)
    compressor, compressed, pending, size = zlib.compressobj(), [], [], 0
    for chunk in encoder.iterencode(value):
        pending.append(chunk)
        size += len(chunk)
        if size >= 65536:
            compressed.append(compressor.compress("".join(pending).encode("utf-8")))
            pending, size = [], 0
    if pending:
        compressed.append(compressor.compress("".join(pending).encode("utf-8")))
    compressed.append(compressor.flush())
    return b"".join(compressed)


def _unpack(value):
    return json.loads(zlib.decompress(value)) if value else None


def _version():
    digest = hashlib.sha256()
    for name in ("backtest_engine.py", "strategies.py", "plateau.py", "research_service.py",
                 "portfolio_config.py", "portfolio_universe.py", "portfolio_signals.py",
                 "portfolio_engine.py", "portfolio_study.py", "portfolio_rotation.py",
                 "portfolio_experiment.py", "portfolio_history.py", "portfolio_actions.py", "portfolio_regions.py", "portfolio_continuous.py", "portfolio_prepared.py", "portfolio_screen.py", "research_names.py", "research_pipeline.py", "portfolio_pool_styles.py"):
        digest.update(name.encode())
        digest.update((Path(__file__).parent / name).read_bytes())
    return digest.hexdigest()


def snapshot_local(directory, symbols, end_date=None):
    """Read only the already user-configured source, never discover local folders."""
    if not isinstance(symbols, list) or not 1 <= len(symbols) <= MAX_SYMBOLS:
        raise ValueError("请选择 1 至 50 只证券作为研究股票池")
    normalized = []
    for symbol in symbols:
        market, code = tdx.symbol_parts(symbol)
        if tdx.security_kind(market, code) != "stock":
            raise ValueError("研究股票池仅支持 A 股股票")
        normalized.append(market + code)
    if len(set(normalized)) != len(normalized):
        raise ValueError("研究股票池包含重复证券")
    cache = Path(directory) / "market.sqlite3"
    if not cache.exists():
        raise ValueError("请先在设置中选择通达信目录并读取行情")
    db = sqlite3.connect(cache.as_uri() + "?mode=ro", uri=True, timeout=10)
    try:
        row = db.execute("SELECT value FROM meta WHERE key='path'").fetchone()
        if not row:
            raise ValueError("请先在设置中选择通达信目录并读取行情")
        root = tdx.normalize_directory(json.loads(row[0]))
        names = {symbol: name for symbol, name in db.execute("SELECT symbol,name FROM securities")}
    finally:
        db.close()
    securities, count = [], 0
    for symbol in normalized:
        bars = tdx.parse_daily(tdx.read_stable(tdx.daily_path(root, symbol)))
        if end_date:
            bars = [bar for bar in bars if bar["date"] <= end_date]
        if not bars:
            raise ValueError(f"{symbol} 在研究截止日期前没有日线")
        count += len(bars)
        if count > MAX_BARS:
            raise ValueError("本次历史数据超过 150000 条，请缩小股票池或截止日期")
        securities.append({"symbol": symbol, "name": names.get(symbol, symbol), "bars": bars})
    dataset = {"securities": securities, "adjustment": "none", "source": "TDX 本地日线冻结快照",
               "warnings": ["未复权日线缺少除权除息、分红与历史证券状态；收益仅用于初步研究。",
                            "显式选择的当前股票池存在选择偏差和幸存者偏差，不代表历史全市场。",
                            "交易日以本次股票池可见日线的日期并集构建，缺失日期不补造行情。"]}
    dataset["fingerprint"] = hashlib.sha256(_dump(dataset).encode()).hexdigest()
    return dataset


class ResearchService:
    def __init__(self, directory, *, autostart=True, workers=MAX_WORKERS, snapshot_loader=None, universe_loader=None):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "research.sqlite3"
        self.lock = threading.RLock()
        self.wake = threading.Event()
        self.closing = threading.Event()
        self.workers = max(1, min(MAX_WORKERS, workers))
        self.snapshot_loader = snapshot_loader or snapshot_local
        self.universe_loader = universe_loader
        self.preparation_lock = threading.Lock()  # Bound full-market snapshot memory.
        self.version = _version()
        self.threads = []
        self.inflight = set()
        with self._db() as db:
            if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("研究数据库损坏，原文件已保留")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS datasets (id TEXT PRIMARY KEY, data BLOB NOT NULL);
                CREATE TABLE IF NOT EXISTS dataset_names (dataset_id TEXT PRIMARY KEY, names TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, request_id TEXT UNIQUE, meta TEXT NOT NULL,
                    dataset_id TEXT NOT NULL, version TEXT NOT NULL,
                    checkpoint BLOB, result BLOB,
                    FOREIGN KEY(dataset_id) REFERENCES datasets(id));
                CREATE TABLE IF NOT EXISTS validation_usage (
                    job_id TEXT NOT NULL, family TEXT NOT NULL, start_date TEXT NOT NULL, end_date TEXT NOT NULL,
                    config_signature TEXT NOT NULL, fingerprint TEXT NOT NULL,
                    PRIMARY KEY(job_id,start_date,end_date));
                CREATE TABLE IF NOT EXISTS research_freezes (id TEXT PRIMARY KEY, signature TEXT UNIQUE NOT NULL, record TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS development_usage (
                    job_id TEXT PRIMARY KEY, family TEXT NOT NULL, start_date TEXT NOT NULL, end_date TEXT NOT NULL);
            """)
            for row in db.execute("SELECT id,meta FROM jobs").fetchall():
                meta = json.loads(row["meta"])
                if meta["status"] in ACTIVE:
                    meta.update(status="stopped" if meta["status"] == "stopping" else "paused",
                                phase="已恢复保存点，等待继续", updatedAt=_now())
                    db.execute("UPDATE jobs SET meta=? WHERE id=?", (_dump(meta), row["id"]))
        if autostart:
            for i in range(self.workers):
                thread = threading.Thread(target=self._worker, name=f"research-{i}", daemon=True)
                self.threads.append(thread)
                thread.start()

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def dispatch(self, method, payload=None):
        payload = payload or {}
        routes = {"research.catalogue": self.catalogue, "research.list": lambda: self.list(payload),
                  "research.create": lambda: self.create(payload),
                  "research.previewSettings": lambda: self.preview_settings(payload),
                  "research.get": lambda: self.get(payload),
                  "research.replay": lambda: self.replay(payload),
                  "research.rename": lambda: self.rename(payload),
                  "research.update": lambda: self.update(payload),
                  "research.delete": lambda: self.delete(payload),
                  "research.control": lambda: self.control(payload)}
        routes.update({"research.freeze": lambda: self.freeze(payload), "research.freezes": self.freezes})
        routes.update({"research.history.status": self.history_status,
                       "research.history.import": lambda: self.import_history(payload),
                       "research.history.importFile": lambda: self.import_history_file(payload),
                       "research.export": lambda: self.export_report(payload)})
        if method not in routes:
            raise ValueError("不支持的研究请求")
        return routes[method]()

    def catalogue(self):
        return {"strategies": catalogue(), "defaults": normalize_config({}),
                "limits": {"maxSymbols": MAX_SYMBOLS, "maxCombinations": 125, "workers": self.workers},
                "portfolio": {"version": portfolio_config.CONFIG_VERSION,
                    "strategies": portfolio_config.catalogue(), "defaults": portfolio_config.default_config(),
                    "pools": portfolio_config.pool_catalogue(),
                    "selection": portfolio_config.normalize_selection(),
                    "poolStyles": portfolio_config.pool_style_catalogue(),
                    "singleExperimentDefaults": {"axes": [], "minNeighborPositiveFraction": .7,
                        "minNeighborhoodSize": 3, "maxNeighborhoodStdPct": 10, "minClosedTrades": 3, "maxDrawdownPct": 30},
                    "singleExperimentLimits": {"maxAxes": 3, "maxValuesPerAxis": 5, "maxTrials": 27},
                    "historySources": self.history_status()["sources"],
                    "experimentDefaults": {"trainDays": 140, "testDays": 39, "stepDays": 39,
                        "capitalMode": "independent",
                        "expanding": False, "maxFolds": 5, "axes": [], "maxTrials": 81,
                        "minNeighborPositiveFraction": .7, "minNeighborhoodSize": 3,
                        "maxNeighborhoodStdPct": 10, "minOosYears": 2, "minOosRegimes": 2},
                    "experimentLimits": {"maxAxes": 8, "maxValuesPerAxis": 5, "maxTrials": 81,
                                         "maxFolds": 8, "maxEngineRuns": 4000},
                    "capabilities": {"wholeUniverse": True, "maxConcurrentPortfolioTasks": 1,
                                     "combinations": ["intersection", "union", "weighted"],
                                     "allocation": ["single", "equal", "soft_rotation", "hard_rotation"], "persistence": True,
                                     "rotation": True, "walkForward": True, "systemPlateau": True,
                                     "rotationRequiresTrainingPersistence": True}},
                "securityNames": self.security_names(), "tasks": self.list()["tasks"]}

    def security_names(self):
        cache = self.directory / "market.sqlite3"
        if not cache.exists():
            return {}
        from contextlib import closing
        try:
            with closing(sqlite3.connect(cache.as_uri() + "?mode=ro", uri=True)) as db:
                return {symbol: name for symbol, name in db.execute("SELECT symbol,name FROM securities") if isinstance(symbol, str) and isinstance(name, str) and name and name not in (symbol, symbol[2:])}
        except sqlite3.Error:
            return {}

    def display_names(self, dataset_id, supplied=None):
        names = self.security_names()
        if supplied:
            return {**names, **{symbol: name for symbol, name in supplied.items() if isinstance(symbol, str) and isinstance(name, str) and name and name not in (symbol, symbol[2:])}}
        if not dataset_id or dataset_id.startswith("pending-"):
            return names
        with self._db() as db:
            stored = db.execute("SELECT names FROM dataset_names WHERE dataset_id=?", (dataset_id,)).fetchone()
            if stored:
                return {**names, **json.loads(stored[0])}
            row = db.execute("SELECT data FROM datasets WHERE id=?", (dataset_id,)).fetchone()
        if row:
            try:
                from .research_names import snapshot_names
                frozen = snapshot_names(row[0])
                with self._db() as db:
                    db.execute("INSERT OR IGNORE INTO dataset_names VALUES (?,?)", (dataset_id, _dump(frozen)))
                names.update(frozen)
            except (ValueError, zlib.error):
                pass  # Display failure never rewrites a result or price snapshot.
        return names

    def list(self, payload=None):
        payload = payload or {}
        query, status = payload.get("query", ""), payload.get("status", "all")
        if not isinstance(query, str) or len(query) > 200 or status not in {"all", "queued", "running", "pausing", "stopping", "paused", "stopped", "completed", "failed"}:
            raise ValueError("任务查询条件无效")
        with self.lock, self._db() as db:
            tasks = []
            for row in db.execute("SELECT meta,dataset_id,result IS NULL AS unstarted FROM jobs"):
                meta = json.loads(row["meta"])
                meta["configEditable"] = bool(meta["kind"] != "frozenValidation" and row["unstarted"] and row["dataset_id"].startswith("pending-")
                    and meta["status"] in ("queued", "paused") and meta["id"] not in self.inflight)
                tasks.append(meta)
        tasks = [t for t in tasks if (status == "all" or t["status"] == status) and (not query or query.lower() in
                 " ".join([t["name"], t["id"], t["kind"], *t.get("symbols", [])]).lower())]
        return {"tasks": sorted(tasks, key=lambda row: (row["createdAt"], row["id"]), reverse=True),
                "workers": self.workers}

    @staticmethod
    def _runner(dataset, meta, state=None):
        if meta["kind"] == "poolPreview":
            from service.portfolio_screen import PortfolioScreen
            return PortfolioScreen(dataset, meta["config"], state=state)
        if meta["kind"] in ("portfolio", "singleBacktest", "frozenValidation"):
            from service.portfolio_engine import PortfolioEngine
            return PortfolioEngine(dataset, meta["config"], state=state)
        if meta["kind"] == "singlePlateau":
            from service.research_pipeline import SinglePlateau
            return SinglePlateau(dataset, meta["config"], meta["experiment"], state=state)
        if meta["kind"] == "comparison":
            from service.portfolio_study import PortfolioStudy
            return PortfolioStudy(dataset, meta["config"], state=state)
        if meta["kind"] in ("walkForward", "systemPlateau"):
            from service.portfolio_experiment import PortfolioExperiment
            return PortfolioExperiment(dataset, meta["config"], meta["experiment"], state=state)
        if meta["kind"] == "plateau":
            return PlateauRunner(dataset, meta["config"], meta["scan"], state=state)
        return BacktestEngine(dataset, meta["config"], state=state)

    def create(self, payload):
        kind = payload.get("kind", "backtest")
        if kind not in {"backtest", "plateau"} | PORTFOLIO_KINDS:
            raise ValueError("不支持的研究类型")
        name = payload.get("name") or {"plateau": "收益平原研究", "backtest": "日线组合回测",
                                      "portfolio": "股票池组合回测", "comparison": "多策略比较与持续性检验",
                                      "walkForward": "滚动样本外验证", "systemPlateau": "全系统收益平原", "poolPreview": "股票池筛选预览",
                                      "singleBacktest": "单策略固定参数回测", "singlePlateau": "关键参数平原", "frozenValidation": "冻结方案新时段验证"}[kind]
        if not isinstance(name, str) or len(name.strip()) > 100:
            raise ValueError("研究名称最多 100 字")
        request_id = payload.get("requestId")
        if request_id is not None and (not isinstance(request_id, str) or not 1 <= len(request_id) <= 100):
            raise ValueError("提交标识无效")
        if request_id:
            with self.lock, self._db() as db:
                old = db.execute("SELECT meta FROM jobs WHERE request_id=?", (request_id,)).fetchone()
                if old:
                    return json.loads(old[0])
        if kind == "frozenValidation":
            return self._create_validation(payload, name.strip(), request_id)
        if kind in PORTFOLIO_KINDS:
            return self._create_portfolio(payload, kind, name.strip(), request_id)
        config = normalize_config(payload.get("config", {}))
        dataset = self.snapshot_loader(self.directory, payload.get("symbols"), config.get("endDate"))
        meta = {"id": str(uuid.uuid4()), "name": name.strip(), "kind": kind, "status": "queued",
                "phase": "等待计算资源", "progress": 0, "createdAt": _now(), "updatedAt": _now(),
                "config": config, "symbols": [item["symbol"] for item in dataset["securities"]],
                "scan": payload.get("scan", {}), "fingerprint": dataset["fingerprint"], "error": None}
        runner = self._runner(dataset, meta)  # Validate before accepting the task.
        state = runner.checkpoint()
        with self.lock, self._db() as db:
            # A duplicate concurrent submission must resolve to the first accepted job.
            if request_id:
                old = db.execute("SELECT meta FROM jobs WHERE request_id=?", (request_id,)).fetchone()
                if old:
                    return json.loads(old[0])
            db.execute("INSERT OR IGNORE INTO datasets VALUES (?,?)", (dataset["fingerprint"], _pack(dataset)))
            db.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?,?)", (meta["id"], request_id, _dump(meta),
                       dataset["fingerprint"], self.version, _pack(state), None))
        self.wake.set()
        return meta

    def freezes(self):
        with self.lock, self._db() as db:
            records = [json.loads(row[0]) for row in db.execute("SELECT record FROM research_freezes")]
        for item in records:
            item["versionCompatible"] = item["codeVersion"] == self.version
        return {"freezes": sorted(records, key=lambda item: (item["createdAt"], item["id"]), reverse=True)}

    def freeze(self, payload):
        """Freeze only observed development evidence, never a client-supplied config."""
        if not isinstance(payload, dict) or set(payload) - {"id", "candidateId", "name"}:
            raise ValueError("冻结只接受已完成任务编号、参数点编号和名称")
        name = payload.get("name")
        if name is not None and (not isinstance(name, str) or not name.strip() or len(name.strip()) > 100):
            raise ValueError("冻结方案名称须为 1 至 100 字")
        with self.lock, self._db() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (payload.get("id"),)).fetchone()
            if not row:
                raise ValueError("开发任务不存在")
            meta, report = json.loads(row["meta"]), _unpack(row["result"])
            if meta["kind"] not in ("singleBacktest", "singlePlateau") or meta["status"] != "completed" or not report:
                raise ValueError("请先完成单策略固定参数回测或关键参数平原，再冻结方案")
            if row["version"] != self.version:
                raise ValueError("计算版本已变化，请重新完成开发研究后冻结")
            config = copy.deepcopy(meta["config"])
            candidate_id = payload.get("candidateId")
            evidence = {"certified": False, "developmentStableRegion": False,
                        "selectionSource": "fixedSingleBacktest", "label": "固定参数开发回测；尚未独立验证"}
            if meta["kind"] == "singlePlateau":
                point = next((item for item in report.get("candidates", []) if item["candidateId"] == candidate_id), None)
                if not point:
                    raise ValueError("请明确选择一个已完成的参数点；系统不会自动冻结最高收益点")
                from service.portfolio_experiment import apply_parameters
                config = apply_parameters(config, point["params"])
                stable = any(region.get("intervalEligible") and candidate_id in region["candidateIds"] for region in report.get("regions", []))
                evidence.update(selectionSource="manualPlateauCandidate", developmentStableRegion=stable,
                                label="人工选择开发期稳定区内参数；尚未独立验证" if stable else "人工选择参数点；未形成稳定开发区，尚未独立验证",
                                metrics=point["metrics"], neighborhood=point["neighborhood"], qualityFailures=point.get("qualityFailures", []), params=point["params"])
            elif candidate_id is not None:
                raise ValueError("固定参数回测没有候选参数点")
            else:
                evidence["metrics"] = report.get("metrics", {})
            from service.research_pipeline import single_config
            config = single_config(config)
            parameter_config = {key: value for key, value in config.items() if key not in ("startDate", "endDate")}
            parameter_hash = hashlib.sha256(_dump({"config": parameter_config, "selection": meta["selection"], "version": self.version}).encode()).hexdigest()
            signature = hashlib.sha256(_dump({"source": meta["id"], "candidate": candidate_id, "parameters": parameter_hash}).encode()).hexdigest()
            old = db.execute("SELECT record FROM research_freezes WHERE signature=?", (signature,)).fetchone()
            if old:
                return {"freeze": json.loads(old[0])}
            record = {"id": str(uuid.uuid4()), "name": name.strip() if name else (meta["name"][:80] + " · 冻结方案"),
                      "createdAt": _now(), "sourceTaskId": meta["id"], "sourceKind": meta["kind"],
                      "candidateId": candidate_id, "developmentStart": config["startDate"], "developmentEnd": config["endDate"],
                      "config": config, "selection": copy.deepcopy(meta["selection"]), "parameterHash": parameter_hash,
                      "codeVersion": self.version, "sourceFingerprint": meta["fingerprint"],
                      "validationFamily": meta.get("validationFamily"), "sourceIdentity": meta.get("sourceIdentity"),
                      "evidence": evidence, "immutable": True}
            db.execute("INSERT INTO research_freezes VALUES (?,?,?)", (record["id"], signature, _dump(record)))
        return {"freeze": record}

    def _validation_settings(self, payload):
        allowed = {"kind", "freezeId", "startDate", "endDate", "name", "requestId"}
        if set(payload) - allowed:
            raise ValueError("冻结验证只接受冻结方案编号和新时段，不能修改策略、漏斗、费用或仓位参数")
        with self.lock, self._db() as db:
            row = db.execute("SELECT record FROM research_freezes WHERE id=?", (payload.get("freezeId"),)).fetchone()
        if not row:
            raise ValueError("请选择已保存的冻结方案")
        frozen = json.loads(row[0])
        if frozen["codeVersion"] != self.version:
            raise ValueError("冻结方案的计算版本已变化；请重新研究并冻结，不能把不同版本结果当作同一方案验证")
        for key in ("startDate", "endDate"):
            value = payload.get(key)
            try:
                if not isinstance(value, str) or dt.date.fromisoformat(value).isoformat() != value:
                    raise ValueError()
            except ValueError:
                raise ValueError("新时段日期须为 YYYY-MM-DD") from None
        if payload["startDate"] <= frozen["developmentEnd"]:
            raise ValueError("验证开始日期必须严格晚于冻结方案的开发截止日期")
        from service.research_pipeline import single_config
        config = single_config({**frozen["config"], "startDate": payload["startDate"], "endDate": payload["endDate"]})
        return frozen, config

    def _create_validation(self, payload, name, request_id):
        frozen, config = self._validation_settings(payload)
        return self._create_portfolio({"name": payload.get("name"), "config": config, "selection": frozen["selection"]},
                                      "frozenValidation", name, request_id, frozen=frozen)

    def _create_portfolio(self, payload, kind, name, request_id, *, frozen=None):
        """Accept quickly; source loading is cancellable background work, not IPC."""
        config, selection, experiment = self._portfolio_settings(payload, kind)
        if not (payload.get("name") or "").strip():
            name = self._portfolio_name(kind, config)
        task_id = str(uuid.uuid4())
        pending_id = "pending-" + task_id
        dataset = {"pendingUniverse": True, "selection": selection}
        meta = {"id": task_id, "name": name, "kind": kind, "status": "queued",
                "phase": "等待冻结股票池与行情", "progress": 0, "createdAt": _now(), "updatedAt": _now(),
                "config": config, "selection": selection, "symbols": [], "symbolCount": None,
                "fingerprint": None, "error": None}
        if experiment is not None:
            meta["experiment"] = experiment
        if kind in ("singleBacktest", "singlePlateau", "frozenValidation"):
            meta["workflow"] = {"version": 1, "stage": kind, "developmentOnly": kind != "frozenValidation"}
        if frozen is not None:
            meta["freeze"] = frozen
            meta["workflow"].update(freezeId=frozen["id"], sourceTaskId=frozen["sourceTaskId"], parameterHash=frozen["parameterHash"])
        with self.lock, self._db() as db:
            if request_id:
                old = db.execute("SELECT meta FROM jobs WHERE request_id=?", (request_id,)).fetchone()
                if old:
                    return json.loads(old[0])
            db.execute("INSERT INTO datasets VALUES (?,?)", (pending_id, _pack(dataset)))
            db.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?,?)", (task_id, request_id, _dump(meta), pending_id, self.version, None, None))
        self.wake.set()
        return meta

    @staticmethod
    def _portfolio_settings(payload, kind):
        normalize = portfolio_config.normalize_screen_config if kind == "poolPreview" else portfolio_config.normalize_config
        config = normalize(payload.get("config", {}))
        if kind in ("singleBacktest", "singlePlateau", "frozenValidation"):
            from service.research_pipeline import single_config
            config = single_config(config)
        selection = portfolio_config.normalize_selection(payload.get("selection", {}))
        selection["lookbackDays"] = config["lookbackDays"]
        if kind == "comparison":
            from service.portfolio_study import normalize_study
            normalize_study(config)  # Validate before accepting costly work.
        experiment = None
        if kind == "singlePlateau":
            from service.research_pipeline import normalize_single_experiment
            experiment = normalize_single_experiment(payload.get("experiment", {}), config)
        if kind in ("walkForward", "systemPlateau"):
            from service.portfolio_experiment import normalize_experiment
            raw_experiment = payload.get("experiment", {})
            if not isinstance(raw_experiment, dict):
                raise ValueError("实验设置须为对象")
            if raw_experiment.get("kind", kind) != kind:
                raise ValueError("实验类型与任务类型不一致")
            experiment = normalize_experiment({**raw_experiment, "kind": kind}, config)
        return config, selection, experiment

    @staticmethod
    def _portfolio_name(kind, config):
        labels = {"portfolio": "组合回测", "comparison": "策略比较", "walkForward": "滚动验证",
                  "systemPlateau": "系统收益平原", "poolPreview": "股票池筛选", "singleBacktest": "单策略固定参数回测",
                  "singlePlateau": "关键参数平原", "frozenValidation": "冻结方案新时段验证"}
        period = (config["startDate"] + " — " + config["endDate"] if config["startDate"] and config["endDate"]
                  else "截至" + config["endDate"] if kind == "poolPreview" and config["endDate"]
                  else "最近" + str(config["lookbackDays"]) + "个交易日")
        if kind == "poolPreview":
            return (labels[kind] + " · " + period)[:100]
        names = {s["id"]: s["name"] for s in portfolio_config.catalogue()}
        strategies = config["allocation"]["strategyIds"]
        title = names.get(strategies[0], "组合策略")
        if len(strategies) > 1:
            title += "等" + str(len(strategies)) + "个策略"
        return (labels[kind] + " · " + title + " · " + period)[:100]

    def preview_settings(self, payload):
        """Validate exactly as creation does, without creating a job or loading prices."""
        kind = payload.get("kind", "portfolio")
        if kind == "frozenValidation":
            frozen, config = self._validation_settings(payload)
            return {"kind": kind, "freeze": frozen, "config": config, "selection": frozen["selection"],
                    "name": payload.get("name") or self._portfolio_name(kind, config), "execution": {"parametersFrozen": True, "holdoutAfter": frozen["developmentEnd"]}}
        if kind not in PORTFOLIO_KINDS:
            raise ValueError("请选择股票池研究任务类型")
        name = payload.get("name", "")
        if not isinstance(name, str) or len(name.strip()) > 100:
            raise ValueError("研究名称最多 100 字")
        config, selection, experiment = self._portfolio_settings(payload, kind)
        result = {"kind": kind, "name": name.strip() or self._portfolio_name(kind, config),
                  "config": config, "selection": selection, "configVersion": portfolio_config.CONFIG_VERSION,
                  "execution": {"signal": "T 日收盘", "fill": "下一交易日开盘", "tPlusOne": True,
                                "poolRefresh": "每个交易日", "datesResolvedAfterSnapshot": not bool(config["startDate"])}}
        if experiment is not None:
            result["experiment"] = experiment
        if kind == "poolPreview":
            result["execution"] = {"screen": "截止交易日收盘", "poolRefresh": "仅所选截止日",
                                   "ordersGenerated": False, "strategyScoring": False,
                                   "datesResolvedAfterSnapshot": not bool(config["endDate"])}
        return result

    def rename(self, payload):
        name = payload.get("name")
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 100:
            raise ValueError("任务名称须为 1 至 100 字")
        with self.lock, self._db() as db:
            row = db.execute("SELECT meta FROM jobs WHERE id=?", (payload.get("id"),)).fetchone()
            if not row:
                raise ValueError("任务不存在")
            meta = json.loads(row[0])
            meta.update(name=name.strip(), updatedAt=_now())
            db.execute("UPDATE jobs SET meta=? WHERE id=?", (_dump(meta), meta["id"]))
        return {"task": meta}

    def update(self, payload):
        task_id = payload.get("id")
        kind = payload.get("kind")
        if kind == "frozenValidation":
            raise ValueError("冻结验证任务不可编辑参数；如需调整日期，请以同一冻结方案另建验证任务")
        if kind not in PORTFOLIO_KINDS:
            raise ValueError("请编辑股票池研究任务；旧版任务保留其原始结果")
        config, selection, experiment = self._portfolio_settings(payload, kind)
        name = payload.get("name")
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 100:
            raise ValueError("任务名称须为 1 至 100 字")
        with self.lock, self._db() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (task_id,)).fetchone()
            if not row:
                raise ValueError("任务不存在")
            meta = json.loads(row["meta"])
            if meta["kind"] == "frozenValidation":
                raise ValueError("冻结验证任务不可改写成其他研究类型")
            if meta["status"] not in ("queued", "paused") or task_id in self.inflight or row["result"] is not None or not row["dataset_id"].startswith("pending-"):
                raise ValueError("任务已冻结行情或开始计算；请复制设置另存新任务，原结果不能被改写")
            meta.update(name=name.strip(), kind=kind, config=config, selection=selection, updatedAt=_now(), error=None)
            meta.pop("experiment", None)
            if experiment is not None:
                meta["experiment"] = experiment
            db.execute("UPDATE datasets SET data=? WHERE id=?", (_pack({"pendingUniverse": True, "selection": selection}), row["dataset_id"]))
            db.execute("UPDATE jobs SET meta=?,version=?,request_id=NULL,checkpoint=NULL WHERE id=?", (_dump(meta), self.version, task_id))
        self.wake.set()
        return {"task": meta}

    def delete(self, payload):
        ids = payload.get("ids")
        if not isinstance(ids, list) or not 1 <= len(ids) <= 200 or any(not isinstance(i, str) for i in ids):
            raise ValueError("请选择 1 至 200 项任务")
        outcomes = []
        with self.lock, self._db() as db:
            for task_id in dict.fromkeys(ids):
                row = db.execute("SELECT meta,dataset_id FROM jobs WHERE id=?", (task_id,)).fetchone()
                if not row:
                    outcomes.append({"id": task_id, "ok": False, "error": "任务不存在"})
                    continue
                status = json.loads(row["meta"])["status"]
                if task_id in self.inflight or status in ("running", "pausing", "stopping"):
                    outcomes.append({"id": task_id, "ok": False, "error": "请先停止任务，等待保存完成后删除"})
                    continue
                db.execute("DELETE FROM jobs WHERE id=?", (task_id,))
                db.execute("DELETE FROM datasets WHERE id=? AND NOT EXISTS(SELECT 1 FROM jobs WHERE dataset_id=?)", (row["dataset_id"], row["dataset_id"]))
                db.execute("DELETE FROM dataset_names WHERE dataset_id NOT IN (SELECT id FROM datasets)")
                # Validation use survives deletion; removing a report cannot
                # make its already-inspected holdout an independent sample.
                outcomes.append({"id": task_id, "ok": True})
        return {"tasks": self.list()["tasks"], "outcomes": outcomes}

    def _source_identity(self, selection):
        """Stable source identity, deliberately independent of newly added bars.

        Local snapshots name their source generically, so the declared family
        alone cannot notice a settings change to another TDX installation. Only
        store a hash of its normalized path, never disclose that path in reports.
        Injected test/custom loaders declare their identity in the dataset family.
        """
        if selection.get("source") == "history":
            identity = {"kind": "history", "sourceId": selection.get("sourceId")}
        elif self.universe_loader is not None:
            return None
        else:
            cache = self.directory / "market.sqlite3"
            if not cache.exists():
                raise ValueError("请先在设置中选择通达信目录并读取行情")
            db = sqlite3.connect(cache.as_uri() + "?mode=ro", uri=True, timeout=10)
            try:
                row = db.execute("SELECT value FROM meta WHERE key='path'").fetchone()
            finally:
                db.close()
            if not row:
                raise ValueError("行情库尚未保存通达信目录")
            root = tdx.normalize_directory(json.loads(row[0]))
            identity = {"kind": "local", "root": os.path.normcase(os.path.realpath(root))}
        return hashlib.sha256(_dump(identity).encode()).hexdigest()

    def _prepare_portfolio(self, meta, pending):
        from service.portfolio_universe import snapshot_universe, UniversePreparationInterrupted
        loader = self.universe_loader or snapshot_universe
        # Only the stored configured source is read; no home-directory discovery.
        with self.preparation_lock:
            with self.lock, self._db() as db:
                current = json.loads(db.execute("SELECT meta FROM jobs WHERE id=?", (meta["id"],)).fetchone()[0])
                if current["status"] in ("stopping", "pausing") or self.closing.is_set():
                    current.update(status="stopped" if current["status"] == "stopping" else "paused", phase="股票池读取前已暂停", updatedAt=_now())
                    db.execute("UPDATE jobs SET meta=? WHERE id=?", (_dump(current), meta["id"]))
                    return None
                current["phase"] = "正在读取完整股票池并冻结行情"
                db.execute("UPDATE jobs SET meta=? WHERE id=?", (_dump(current), meta["id"]))
            def cancelled():
                with self.lock, self._db() as db:
                    current = json.loads(db.execute("SELECT meta FROM jobs WHERE id=?", (meta["id"],)).fetchone()[0])
                return self.closing.is_set() or current["status"] in ("pausing", "stopping")

            def progress(done, total):
                with self.lock, self._db() as db:
                    current = json.loads(db.execute("SELECT meta FROM jobs WHERE id=?", (meta["id"],)).fetchone()[0])
                    current.update(phase=f"冻结股票池：已处理 {done} / {total} 个文件", updatedAt=_now(),
                                   preparation={"processedFiles": done, "totalFiles": total})
                    db.execute("UPDATE jobs SET meta=? WHERE id=?", (_dump(current), meta["id"]))

            try:
                # Source identity is a new-workflow freeze invariant. Legacy
                # portfolio preparation keeps its loader contract (including
                # cancellable custom loaders that do not use market.sqlite3).
                binds_source = meta["kind"] in ("singleBacktest", "singlePlateau", "frozenValidation")
                source_identity = self._source_identity(pending["selection"]) if binds_source else None
                if meta.get("freeze") and meta["freeze"].get("sourceIdentity") != source_identity:
                    raise ValueError("当前行情来源与冻结方案不一致，请恢复原来源；不同来源不能作为同一冻结方案的新时段验证")
                options = {"progress": progress, "cancelled": cancelled} if self.universe_loader is None else {}
                dataset = loader(self.directory, pending["selection"], meta["config"].get("endDate"), meta["config"].get("startDate"), **options)
                if binds_source and self._source_identity(pending["selection"]) != source_identity:
                    raise ValueError("读取行情期间数据来源发生变化，请确认设置后重新创建任务")
            except UniversePreparationInterrupted:
                with self.lock, self._db() as db:
                    current = json.loads(db.execute("SELECT meta FROM jobs WHERE id=?", (meta["id"],)).fetchone()[0])
                    current.update(status="stopped" if current["status"] == "stopping" else "paused",
                                   phase="读取已中断；继续时重新冻结完整股票池", updatedAt=_now())
                    db.execute("UPDATE jobs SET meta=? WHERE id=?", (_dump(current), meta["id"]))
                return None
            config = copy.deepcopy(meta["config"])
            calendar = sorted(set(dataset.get("calendar") or [b["date"] for s in dataset["securities"] for b in s["bars"]]))
            eligible = [d for d in calendar if (not config["startDate"] or d >= config["startDate"]) and (not config["endDate"] or d <= config["endDate"])]
            if not eligible:
                raise ValueError("所选区间没有可用交易日")
            config["endDate"] = config["endDate"] or eligible[-1]
            config["startDate"] = config["startDate"] or eligible[-min(config["lookbackDays"], len(eligible))]
            dataset["fingerprint"] = dataset.get("fingerprint") or hashlib.sha256(_dump(dataset).encode()).hexdigest()
            meta["config"] = config
            family = {"source": dataset.get("historyDeclaration", {}).get("source", dataset.get("source")),
                      "universe": dataset.get("historyDeclaration", {}).get("universe", "local-file-coverage"),
                      "scope": "declared source; overlapping dates tracked even when the selected stock subset changes"}
            validation_family = hashlib.sha256(_dump(family).encode()).hexdigest()
            if meta.get("freeze") and meta["freeze"].get("validationFamily") != validation_family:
                raise ValueError("当前数据声明的来源或历史股票范围与冻结方案不一致，请恢复原来源后验证新时段")
            runner = self._runner(dataset, meta)
            # The public research identity ignores operational batch size. A
            # stored snapshot includes that metadata, so address its complete
            # content separately; otherwise a second batch-size choice could
            # restore a checkpoint against the first task's different bytes.
            storage_id = "portfolio-" + (getattr(runner, "data_digest", None) or getattr(runner, "digest", None))
            dataset_blob, checkpoint_blob = _pack(dataset), _pack(runner.checkpoint())
            with self.lock, self._db() as db:
                row = db.execute("SELECT meta,dataset_id FROM jobs WHERE id=?", (meta["id"],)).fetchone()
                current = json.loads(row["meta"])
                symbols = [s["symbol"] for s in dataset["securities"]]
                current.update(config=config, symbols=symbols if pending["selection"]["mode"] == "explicit" else [],
                               symbolCount=len(symbols), fingerprint=dataset["fingerprint"],
                               universeCoverage=dataset.get("universeCoverage"), phase="股票池已冻结，开始计算", updatedAt=_now())
                current["validationFamily"] = validation_family
                current["sourceIdentity"] = source_identity
                if current["status"] in ("pausing", "stopping") or self.closing.is_set():
                    current["status"] = "stopped" if current["status"] == "stopping" else "paused"
                db.execute("INSERT OR IGNORE INTO datasets VALUES (?,?)", (storage_id, dataset_blob))
                db.execute("UPDATE jobs SET meta=?,dataset_id=?,checkpoint=? WHERE id=?",
                           (_dump(current), storage_id, checkpoint_blob, meta["id"]))
                db.execute("DELETE FROM datasets WHERE id=? AND id NOT IN (SELECT dataset_id FROM jobs)", (row["dataset_id"],))
            if current["status"] != "running":
                return None
            return current, dataset, runner

    def get(self, payload):
        task_id = payload.get("id")
        if not isinstance(task_id, str):
            raise ValueError("请选择研究任务")
        offsets = {key: payload.get(key + "Offset", 0) for key in ("decision", "trade", "closedTrade", "daily", "rotation")}
        size = payload.get("pageSize", 200)
        daily_size = payload.get("dailyPageSize", min(size, 20) if type(size) is int else 20)
        if type(size) is not int or not 1 <= size <= 1000 or type(daily_size) is not int or not 1 <= daily_size <= 200 or any(type(v) is not int or v < 0 for v in offsets.values()):
            raise ValueError("分页范围无效")
        with self.lock, self._db() as db:
            row = db.execute("SELECT meta,result,dataset_id FROM jobs WHERE id=?", (task_id,)).fetchone()
            if not row:
                raise ValueError("研究任务不存在")
        result = _unpack(row["result"])
        meta = json.loads(row["meta"])
        meta["configEditable"] = next((t["configEditable"] for t in self.list()["tasks"] if t["id"] == task_id), False)
        if isinstance(result, dict):
            names = self.display_names(row["dataset_id"], result.get("securityNames"))
            if names:
                result["securityNames"] = names
            if isinstance(result.get("screenPools"), dict):
                from .portfolio_screen import paginate_screen
                result.update(paginate_screen(result, payload, names))
        result_path = payload.get("resultPath")
        event_date = payload.get("eventDate")
        if event_date is not None:
            try:
                if dt.date.fromisoformat(event_date).isoformat() != event_date:
                    raise ValueError()
            except (ValueError, TypeError):
                raise ValueError("操作日期须为 YYYY-MM-DD") from None
        if result_path is not None:
            if not isinstance(result_path, str) or len(result_path) > 512:
                raise ValueError("研究明细路径无效")
            selected = result
            try:
                for part in result_path.split(".") if result_path else []:
                    if isinstance(selected, list) and part.isdigit():
                        selected = selected[int(part)]
                    elif isinstance(selected, dict) and part in selected:
                        selected = selected[part]
                    else:
                        raise KeyError(part)
                if not isinstance(selected, dict):
                    raise KeyError(result_path)
            except (KeyError, IndexError):
                raise ValueError("研究明细路径不存在") from None
        if isinstance(result, dict):
            result["taskStatus"] = meta["status"]
            result["complete"] = meta["status"] == "completed"
        def page_records(value, path=""):
            if isinstance(value, list):
                for index, child in enumerate(value):
                    page_records(child, f"{path}.{index}")
                return
            if not isinstance(value, dict):
                return
            pagination = {}
            for key, singular in (("decisions", "decision"), ("trades", "trade"), ("closedTrades", "closedTrade"), ("daily", "daily"), ("rotationLog", "rotation"), ("actionLedger", "trade")):
                if isinstance(value.get(key), list):
                    if event_date and key in ("decisions", "trades") and (result_path is None or path == result_path):
                        value[key] = [item for item in value[key] if item.get("date") == event_date]
                    offset = offsets[singular] if result_path is None or path == result_path else 0
                    total = len(value[key])
                    page_size = daily_size if key == "daily" else size
                    value[key] = value[key][offset:offset + page_size]
                    pagination[key] = {"offset": offset, "total": total, "pageSize": page_size, "hasMore": offset + page_size < total}
            for key, child in value.items():
                if isinstance(child, (dict, list)):
                    page_records(child, f"{path}.{key}" if path else key)
            if pagination:
                pagination["path"] = path
                value["pagination"] = pagination
                if event_date:
                    pagination["eventDate"] = event_date
        page_records(result)
        return {"task": meta, "result": result}

    def history_status(self):
        from .portfolio_history import HistoryStore
        return {"sources": HistoryStore(self.directory).list()}

    def import_history(self, payload):
        from .portfolio_history import HistoryStore
        summary = HistoryStore(self.directory).import_package(payload.get("package"))
        return {"imported": summary, **self.history_status()}

    def import_history_file(self, payload):
        # Only the desktop main process exposes the file picker; renderer's
        # generic business IPC rejects this private path-bearing method.
        path = Path(payload.get("path", ""))
        extension = path.suffix.lower()
        maximum = 2 * 1024**3 if extension in (".jsonl", ".ndjson") else 512 * 1024**2
        if not path.is_file() or extension not in (".json", ".jsonl", ".ndjson") or path.stat().st_size > maximum:
            raise ValueError("请选择 JSON（最多 512 MiB）或逐行 JSONL/NDJSON（最多 2 GiB）历史包")
        with path.open("r", encoding="utf-8-sig") as stream:
            if extension == ".json":
                package = json.load(stream)
            else:
                import sys
                first = stream.readline(16 * 1024**2 + 1)
                if len(first) > 16 * 1024**2:
                    raise ValueError("JSONL 首行超出 16 MiB")
                package = json.loads(first)
                if not isinstance(package, dict) or not isinstance(package.get("dataset"), dict):
                    raise ValueError("JSONL 首行须为历史包头部（包含 dataset.calendar）")
                dataset = package["dataset"]
                securities = dataset.setdefault("securities", [])
                members = dataset.setdefault("membershipByDate", {})
                while True:
                    line = stream.readline(16 * 1024**2 + 1)
                    if not line:
                        break
                    if len(line) > 16 * 1024**2:
                        raise ValueError("JSONL 单条证券或成分记录超出 16 MiB")
                    if not line.strip():
                        continue
                    record = json.loads(line)
                    if not isinstance(record, dict):
                        raise ValueError("JSONL 记录须为对象")
                    if set(record) == {"security"}:
                        securities.append(record["security"])
                    elif set(record) == {"date", "members"} and isinstance(record["members"], list):
                        if record["date"] in members:
                            raise ValueError("JSONL 成分日期重复")
                        members[record["date"]] = [sys.intern(symbol) for symbol in record["members"]]
                    else:
                        raise ValueError("JSONL 记录须为 security 或 date/members")
        from .portfolio_history import HistoryStore
        summary = HistoryStore(self.directory).import_package(package, owned=True)
        return {"imported": summary, **self.history_status()}

    def export_report(self, payload):
        task_id = payload.get("id")
        try:
            uuid.UUID(task_id)
        except (ValueError, TypeError, AttributeError):
            raise ValueError("请选择有效研究任务") from None
        with self.lock, self._db() as db:
            row = db.execute("SELECT meta,result,dataset_id FROM jobs WHERE id=?", (task_id,)).fetchone()
        if not row or not row["result"]:
            raise ValueError("研究任务尚无可导出结果")
        report = {"task": json.loads(row["meta"]), "result": _unpack(row["result"]),
                  "export": {"completeAudit": True, "snapshotAt": _now(), "paginationApplied": False}}
        if isinstance(report["result"], dict):
            names = self.display_names(row["dataset_id"], report["result"].get("securityNames"))
            if names:
                report["result"]["securityNames"] = names
        directory = self.directory / "research-exports"
        directory.mkdir(exist_ok=True)
        target = directory / (task_id + ".json")
        descriptor, temporary = tempfile.mkstemp(prefix="report-", suffix=".tmp", dir=directory)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(report, stream, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return {"path": str(target), "bytes": target.stat().st_size, "completeAudit": True,
                "taskComplete": report["task"]["status"] == "completed"}

    def _validation_audit(self, meta, result):
        family = meta.get("validationFamily", meta.get("fingerprint"))
        if meta["kind"] in PORTFOLIO_KINDS - {"poolPreview", "frozenValidation"} and meta["config"].get("startDate") and meta["config"].get("endDate"):
            with self.lock, self._db() as db:
                db.execute("INSERT OR IGNORE INTO development_usage VALUES (?,?,?,?)",
                           (meta["id"], family, meta["config"]["startDate"], meta["config"]["endDate"]))
        intervals = set()
        def visit(value):
            if not isinstance(value, dict):
                return
            for run in value.get("testing", {}).values():
                config = run.get("config", {}) if isinstance(run, dict) else {}
                if config.get("startDate") and config.get("endDate"):
                    intervals.add((config["startDate"], config["endDate"]))
            for fold in value.get("folds", []):
                visit(fold)
            visit(value.get("currentFold"))
            continuous = value.get("continuous")
            if isinstance(continuous, dict):
                for run in continuous.get("runs", {}).values():
                    curve = run.get("curve", [])
                    if curve:
                        intervals.add((curve[0]["date"], curve[-1]["date"]))
        visit(result)
        if meta["kind"] == "frozenValidation" and result.get("curve"):
            intervals.add((meta["config"]["startDate"], meta["config"]["endDate"]))
        if not intervals:
            return
        signature = hashlib.sha256(_dump({"config": meta["config"], "experiment": meta.get("experiment")}).encode()).hexdigest()
        reused, reproductions, developed = [], [], []
        with self.lock, self._db() as db:
            previous = db.execute("SELECT * FROM validation_usage WHERE family=? AND job_id<>?", (family, meta["id"])).fetchall()
            development = db.execute("SELECT * FROM development_usage WHERE family=? AND job_id<>?", (family, meta["id"])).fetchall()
            # Also recognize reports created before the development-use ledger was
            # added. Deleted legacy reports and external inspection remain unknown.
            legacy_development = []
            if meta["kind"] == "frozenValidation":
                known_ids = {item["job_id"] for item in development}
                for old_row in db.execute("SELECT id,meta FROM jobs WHERE result IS NOT NULL AND id<>?", (meta["id"],)):
                    old = json.loads(old_row["meta"])
                    config = old.get("config", {})
                    if (old_row["id"] not in known_ids and old.get("validationFamily", old.get("fingerprint")) == family
                            and old["kind"] not in ("poolPreview", "frozenValidation") and config.get("startDate") and config.get("endDate")):
                        legacy_development.append({"job_id": old_row["id"], "start_date": config["startDate"], "end_date": config["endDate"]})
            for start, end in sorted(intervals):
                if meta["kind"] == "frozenValidation":
                    for old in [*development, *legacy_development]:
                        if start <= old["end_date"] and end >= old["start_date"]:
                            developed.append({"priorJobId": old["job_id"], "startDate": max(start, old["start_date"]), "endDate": min(end, old["end_date"])})
                for old in previous:
                    if start <= old["end_date"] and end >= old["start_date"]:
                        item = {"priorJobId": old["job_id"], "startDate": max(start, old["start_date"]), "endDate": min(end, old["end_date"])}
                        if signature == old["config_signature"] and meta["fingerprint"] == old["fingerprint"]:
                            reproductions.append(item)
                        else:
                            reused.append(item)
                db.execute("INSERT OR IGNORE INTO validation_usage VALUES (?,?,?,?,?,?)", (meta["id"], family, start, end, signature, meta["fingerprint"]))
        result["validationAudit"] = {"status": "developmentSample" if reused or developed else "reproduction" if reproductions else "fresh",
                                     "reusedIntervals": reused, "reproductionIntervals": reproductions,
                                     "priorDevelopmentIntervals": developed,
                                     "independent": not (reused or reproductions or developed),
                                     "reasons": (["previously_used_development_period"] if developed else []) + (["holdout_reused_with_changed_settings"] if reused else []) + (["same_period_reproduction"] if reproductions else []),
                                     "trackedIntervals": [list(pair) for pair in sorted(intervals)],
                                     "scope": "same declared source/universe within this workspace; external prior use and deleted legacy development reports cannot be detected"}
        if reused or developed:
            message = "该样本外区间此前已用不同设置计算，现在标记为开发样本；正式验证请使用未使用区间。"
            result.setdefault("warnings", []).append(message)
            if isinstance(result.get("rotationGate"), dict):
                result["rotationGate"].update(passed=False, status="unverified", conclusion=message)
            for child in [*result.get("folds", []), result.get("currentFold"), result.get("continuous")]:
                if isinstance(child, dict) and isinstance(child.get("rotationGate"), dict):
                    child["rotationGate"].update(passed=False, status="unverified", conclusion=message)
            if isinstance(result.get("plateau"), dict):
                result["plateau"].update(certified=False, recommendedRanges=[], recommendedCartesianComplete=False, conclusion=message)
                result["plateau"].setdefault("reasons", []).append("holdout_reused_with_changed_settings")
                validation = result["plateau"].setdefault("statisticalValidation", {})
                validation["passed"] = False
                validation.setdefault("reasons", []).append("holdout_reused_with_changed_settings")

    def replay(self, payload):
        """Rebuild a scanned point's training path from the original frozen input."""
        task_id, candidate_id = payload.get("id"), payload.get("candidateId")
        if not isinstance(task_id, str) or not isinstance(candidate_id, str):
            raise ValueError("请选择平原任务和参数点")
        with self.lock, self._db() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (task_id,)).fetchone()
            if not row:
                raise ValueError("研究任务不存在")
            parent, report = json.loads(row["meta"]), _unpack(row["result"])
            if row["version"] != self.version:
                raise ValueError("引擎版本已变化，不能把新运行当作原参数点复现")
            if parent["kind"] == "singlePlateau":
                return self._replay_single(row, parent, report, candidate_id)
            if parent["kind"] != "plateau" or parent["status"] != "completed" or not report:
                raise ValueError("请等待收益平原完成后复核参数路径")
            candidate = next((c for c in report["candidates"] if c["id"] == candidate_id), None)
            if not candidate:
                raise ValueError("参数点不存在")
            dataset = _unpack(db.execute("SELECT data FROM datasets WHERE id=?", (row["dataset_id"],)).fetchone()[0])
        config = copy.deepcopy(candidate["config"])
        blocks = report["split"]["blocks"]
        if not blocks or not candidate.get("blocks"):
            raise ValueError("数据不足或此参数尚未完成验证块，不能创建训练路径")
        config.update(startDate=blocks[0]["startDate"], endDate=blocks[-1]["endDate"])
        meta = {"id": str(uuid.uuid4()), "name": parent["name"][:65] + " · 参数路径 " + candidate_id,
                "kind": "backtest", "status": "queued", "phase": "等待计算资源", "progress": 0,
                "createdAt": _now(), "updatedAt": _now(), "config": config, "symbols": parent["symbols"],
                "scan": {}, "fingerprint": dataset["fingerprint"], "error": None,
                "parentId": task_id, "candidateId": candidate_id,
                "replayNote": "训练区间连续资金路径；与各验证块独立重置资金的评分口径不同，不参与原平原重排。"}
        runner = self._runner(dataset, meta)
        with self.lock, self._db() as db:
            db.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?,?)", (meta["id"], None, _dump(meta), dataset["fingerprint"],
                       self.version, _pack(runner.checkpoint()), None))
        self.wake.set()
        return meta

    def _replay_single(self, row, parent, report, candidate_id):
        if parent["status"] != "completed" or not report:
            raise ValueError("请等待关键参数平原完成后复核参数路径")
        candidate = next((item for item in report.get("candidates", []) if item["candidateId"] == candidate_id), None)
        if not candidate:
            raise ValueError("参数点不存在")
        from service.portfolio_experiment import apply_parameters
        config = apply_parameters(parent["config"], candidate["params"])
        with self.lock, self._db() as db:
            dataset = _unpack(db.execute("SELECT data FROM datasets WHERE id=?", (row["dataset_id"],)).fetchone()[0])
            meta = {"id": str(uuid.uuid4()), "name": parent["name"][:65] + " · 参数路径 " + candidate_id,
                    "kind": "singleBacktest", "status": "queued", "phase": "等待计算资源", "progress": 0,
                    "createdAt": _now(), "updatedAt": _now(), "config": config, "selection": parent["selection"],
                    "symbols": parent["symbols"], "symbolCount": parent.get("symbolCount"), "fingerprint": parent["fingerprint"],
                    "error": None, "parentId": parent["id"], "candidateId": candidate_id,
                    "validationFamily": parent.get("validationFamily"),
                    "sourceIdentity": parent.get("sourceIdentity"),
                    "workflow": {"version": 1, "stage": "singleBacktest", "developmentOnly": True, "sourceTaskId": parent["id"]},
                    "replayNote": "同一冻结行情与开发区间的单参数明细复现，不属于新验证样本。"}
            runner = self._runner(dataset, meta)
            db.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?,?)", (meta["id"], None, _dump(meta), row["dataset_id"],
                       self.version, _pack(runner.checkpoint()), None))
        self.wake.set()
        return meta

    def control(self, payload):
        ids, action = payload.get("ids"), payload.get("action")
        if not isinstance(ids, list) or not ids or len(ids) > 200 or any(not isinstance(i, str) for i in ids):
            raise ValueError("请选择 1 至 200 项任务")
        if action not in ("pause", "resume", "stop"):
            raise ValueError("无效的任务动作")
        outcomes = []
        with self.lock, self._db() as db:
            for task_id in dict.fromkeys(ids):
                row = db.execute("SELECT meta,version FROM jobs WHERE id=?", (task_id,)).fetchone()
                error = None
                if not row:
                    outcomes.append({"id": task_id, "ok": False, "error": "任务不存在"})
                    continue
                meta = json.loads(row["meta"])
                status = meta["status"]
                if action == "resume" and status == "paused":
                    if row["version"] != self.version:
                        error = "策略或引擎版本已改变，请以保存的配置创建新任务，原结果仍可查看"
                    else:
                        meta.update(status="queued", phase="等待计算资源")
                elif action == "pause" and status in ("queued", "running"):
                    meta.update(status="pausing" if task_id in self.inflight else "paused", phase="正在保存暂停点")
                elif action == "stop" and status in ("queued", "running", "paused", "pausing"):
                    meta.update(status="stopping" if task_id in self.inflight else "stopped", phase="已停止（结果未完成）")
                else:
                    error = "此状态不支持该动作"
                if not error:
                    meta["updatedAt"] = _now()
                    db.execute("UPDATE jobs SET meta=? WHERE id=?", (_dump(meta), task_id))
                outcomes.append({"id": task_id, "ok": error is None, **({"error": error} if error else {})})
        self.wake.set()
        return {"tasks": self.list()["tasks"], "outcomes": outcomes}

    def _claim(self):
        while True:
            claimed = None
            with self.lock, self._db() as db:
                rows = db.execute("SELECT id,meta,version FROM jobs ORDER BY rowid").fetchall()
                portfolio_busy = any(row["id"] in self.inflight and json.loads(row["meta"])["kind"] in PORTFOLIO_KINDS for row in rows)
                for row in rows:
                    meta = json.loads(row["meta"])
                    if meta["status"] != "queued" or row["id"] in self.inflight:
                        continue
                    if portfolio_busy and meta["kind"] in PORTFOLIO_KINDS:
                        continue
                    if row["version"] != self.version:
                        meta.update(status="paused", phase="版本已变化，请创建新任务")
                        db.execute("UPDATE jobs SET meta=? WHERE id=?", (_dump(meta), row["id"]))
                        continue
                    saved = db.execute("SELECT dataset_id,checkpoint FROM jobs WHERE id=?", (row["id"],)).fetchone()
                    data = db.execute("SELECT data FROM datasets WHERE id=?", (saved["dataset_id"],)).fetchone()
                    meta.update(status="running", phase="正在恢复计算", updatedAt=_now())
                    db.execute("UPDATE jobs SET meta=? WHERE id=?", (_dump(meta), row["id"]))
                    self.inflight.add(row["id"])
                    claimed = meta, data[0] if data else None, saved["checkpoint"]
                    break
            if claimed is None:
                return None
            meta, dataset_blob, checkpoint_blob = claimed
            try:
                dataset, checkpoint = _unpack(dataset_blob), _unpack(checkpoint_blob)
                if not isinstance(dataset, dict):
                    raise ValueError("缺少输入快照")
                return meta, dataset, checkpoint
            except Exception:
                with self.lock, self._db() as db:
                    meta.update(status="failed", phase="保存点无法读取，原记录已保留", updatedAt=_now(),
                                error="研究快照或保存点损坏，请检查备份。")
                    db.execute("UPDATE jobs SET meta=? WHERE id=?", (_dump(meta), meta["id"]))
                    self.inflight.discard(meta["id"])

    @staticmethod
    def _progress(runner, result, done):
        progress = getattr(runner, "progress", None)
        if callable(progress):
            progress = progress()
        if isinstance(progress, dict):
            phase = progress.get("phase", progress.get("stage", "计算中"))
            progress = progress.get("progress", progress.get("fraction", progress.get("percent", 0) / 100))
        else:
            phase = result.get("phase", "日线模拟")
        if progress is None:
            progress = result.get("progress", 0)
        return (1 if done else min(.999999, max(0, float(progress or 0)))), phase

    def _execute(self, claim):
        meta, dataset, state = claim
        task_id = meta["id"]
        try:
            with self.lock, self._db() as db:
                current = json.loads(db.execute("SELECT meta FROM jobs WHERE id=?", (task_id,)).fetchone()[0])
                if current["status"] in ("pausing", "stopping") or self.closing.is_set():
                    current.update(status="stopped" if current["status"] == "stopping" else "paused",
                                   phase="计算开始前已保存暂停状态", updatedAt=_now())
                    db.execute("UPDATE jobs SET meta=? WHERE id=?", (_dump(current), task_id))
                    return
            if dataset.get("pendingUniverse"):
                prepared = self._prepare_portfolio(meta, dataset)
                if prepared is None:
                    return
                meta, dataset, runner = prepared
            else:
                runner = self._runner(dataset, meta, state)
            names = {s["symbol"]: s["name"] for s in dataset.get("securities", []) if isinstance(s.get("name"), str) and s["name"] and s["name"] not in (s["symbol"], s["symbol"][2:])}
            while True:
                done = runner.step(days=5)
                result = runner.result()
                result["kind"] = meta["kind"]
                if meta.get("workflow"):
                    result["workflow"] = copy.deepcopy(meta["workflow"])
                if meta["kind"] == "frozenValidation":
                    result["freeze"] = copy.deepcopy(meta["freeze"])
                    result.setdefault("warnings", []).append("本次仅验证已冻结方案，不会依据新时段收益重新选择参数；样本是否盈利与是否独立分别判断。")
                if names:
                    result["securityNames"] = names
                self._validation_audit(meta, result)
                state = runner.checkpoint()
                progress, phase = self._progress(runner, result, done)
                # Encoding must not hold the control lock: pause/stop can be
                # accepted while large audit paths are being compressed.
                state_blob, result_blob = _pack(state), _pack(result)
                with self.lock, self._db() as db:
                    latest = json.loads(db.execute("SELECT meta FROM jobs WHERE id=?", (task_id,)).fetchone()[0])
                    status = latest["status"]
                    if status == "stopping":
                        status, phase = "stopped", "已停止（结果未完成）"
                    elif done:
                        status, phase = "completed", ("数据不足（未执行扫描）" if result.get("status") == "insufficient-data" else "研究完成")
                    elif status == "pausing" or self.closing.is_set():
                        status, phase = "paused", "已保存暂停点"
                    latest.update(status=status, phase=phase, progress=progress, updatedAt=_now())
                    db.execute("UPDATE jobs SET meta=?,checkpoint=?,result=? WHERE id=?",
                               (_dump(latest), state_blob, result_blob, task_id))
                if status != "running":
                    break
        except Exception as error:
            with self.lock, self._db() as db:
                current = json.loads(db.execute("SELECT meta FROM jobs WHERE id=?", (task_id,)).fetchone()[0])
                current.update(status="failed", phase="计算失败，保留最近保存点", updatedAt=_now(),
                               error=str(error) if isinstance(error, ValueError) else "研究计算失败，输入快照与已有结果已保留。")
                db.execute("UPDATE jobs SET meta=? WHERE id=?", (_dump(current), task_id))
        finally:
            with self.lock:
                self.inflight.discard(task_id)

    def _worker(self):
        while not self.closing.is_set():
            claim = self._claim()
            if claim:
                self._execute(claim)
            else:
                self.wake.wait(.25)
                self.wake.clear()

    def run_next(self):
        """Deterministic test/CLI hook; identical execution path to worker threads."""
        claim = self._claim()
        if claim:
            self._execute(claim)
        return claim is not None

    def close(self):
        self.closing.set()
        self.wake.set()
        for thread in self.threads:
            thread.join(timeout=2)
