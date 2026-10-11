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
from pathlib import Path
import sqlite3
import threading
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


def _now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds")


def _dump(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True)


def _pack(value):
    return zlib.compress(_dump(value).encode("utf-8"))


def _unpack(value):
    return json.loads(zlib.decompress(value)) if value else None


def _version():
    digest = hashlib.sha256()
    for name in ("backtest_engine.py", "strategies.py", "plateau.py", "research_service.py",
                 "portfolio_config.py", "portfolio_universe.py", "portfolio_signals.py",
                 "portfolio_engine.py", "portfolio_study.py"):
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
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, request_id TEXT UNIQUE, meta TEXT NOT NULL,
                    dataset_id TEXT NOT NULL, version TEXT NOT NULL,
                    checkpoint BLOB, result BLOB,
                    FOREIGN KEY(dataset_id) REFERENCES datasets(id));
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
        routes = {"research.catalogue": self.catalogue, "research.list": self.list,
                  "research.create": lambda: self.create(payload),
                  "research.get": lambda: self.get(payload),
                  "research.replay": lambda: self.replay(payload),
                  "research.control": lambda: self.control(payload)}
        if method not in routes:
            raise ValueError("不支持的研究请求")
        return routes[method]()

    def catalogue(self):
        return {"strategies": catalogue(), "defaults": normalize_config({}),
                "limits": {"maxSymbols": MAX_SYMBOLS, "maxCombinations": 125, "workers": self.workers},
                "portfolio": {"version": portfolio_config.CONFIG_VERSION,
                    "strategies": portfolio_config.catalogue(), "defaults": portfolio_config.default_config(),
                    "selection": portfolio_config.normalize_selection(),
                    "capabilities": {"wholeUniverse": True, "maxConcurrentPortfolioTasks": 1,
                                     "combinations": ["intersection", "union", "weighted"],
                                     "allocation": ["single", "equal"], "persistence": True,
                                     "rotation": False, "systemPlateau": False}},
                "tasks": self.list()["tasks"]}

    def list(self):
        with self.lock, self._db() as db:
            tasks = [json.loads(row[0]) for row in db.execute("SELECT meta FROM jobs")]
        return {"tasks": sorted(tasks, key=lambda row: (row["createdAt"], row["id"]), reverse=True),
                "workers": self.workers}

    @staticmethod
    def _runner(dataset, meta, state=None):
        if meta["kind"] == "portfolio":
            from service.portfolio_engine import PortfolioEngine
            return PortfolioEngine(dataset, meta["config"], state=state)
        if meta["kind"] == "comparison":
            from service.portfolio_study import PortfolioStudy
            return PortfolioStudy(dataset, meta["config"], state=state)
        if meta["kind"] == "plateau":
            return PlateauRunner(dataset, meta["config"], meta["scan"], state=state)
        return BacktestEngine(dataset, meta["config"], state=state)

    def create(self, payload):
        kind = payload.get("kind", "backtest")
        if kind not in ("backtest", "plateau", "portfolio", "comparison"):
            raise ValueError("不支持的研究类型")
        name = payload.get("name") or {"plateau": "收益平原研究", "backtest": "日线组合回测",
                                      "portfolio": "股票池组合回测", "comparison": "多策略比较与持续性检验"}[kind]
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
        if kind in ("portfolio", "comparison"):
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

    def _create_portfolio(self, payload, kind, name, request_id):
        """Accept quickly; source loading is cancellable background work, not IPC."""
        config = portfolio_config.normalize_config(payload.get("config", {}))
        selection = portfolio_config.normalize_selection(payload.get("selection", {}))
        selection["lookbackDays"] = config["lookbackDays"]
        if kind == "comparison":
            from service.portfolio_study import normalize_study
            normalize_study(config)  # Validate before accepting costly work.
        task_id = str(uuid.uuid4())
        pending_id = "pending-" + task_id
        dataset = {"pendingUniverse": True, "selection": selection}
        meta = {"id": task_id, "name": name, "kind": kind, "status": "queued",
                "phase": "等待冻结股票池与行情", "progress": 0, "createdAt": _now(), "updatedAt": _now(),
                "config": config, "selection": selection, "symbols": [], "symbolCount": None,
                "fingerprint": None, "error": None}
        with self.lock, self._db() as db:
            if request_id:
                old = db.execute("SELECT meta FROM jobs WHERE request_id=?", (request_id,)).fetchone()
                if old:
                    return json.loads(old[0])
            db.execute("INSERT INTO datasets VALUES (?,?)", (pending_id, _pack(dataset)))
            db.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?,?)", (task_id, request_id, _dump(meta), pending_id, self.version, None, None))
        self.wake.set()
        return meta

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
                options = {"progress": progress, "cancelled": cancelled} if self.universe_loader is None else {}
                dataset = loader(self.directory, pending["selection"], meta["config"].get("endDate"), meta["config"].get("startDate"), **options)
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
            row = db.execute("SELECT meta,result FROM jobs WHERE id=?", (task_id,)).fetchone()
            if not row:
                raise ValueError("研究任务不存在")
        result = _unpack(row["result"])
        meta = json.loads(row["meta"])
        if isinstance(result, dict):
            result["taskStatus"] = meta["status"]
            result["complete"] = meta["status"] == "completed"
        def page_records(value):
            if not isinstance(value, dict):
                return
            pagination = {}
            for key, singular in (("decisions", "decision"), ("trades", "trade"), ("closedTrades", "closedTrade"), ("daily", "daily"), ("rotationLog", "rotation")):
                if isinstance(value.get(key), list):
                    offset = offsets[singular]
                    total = len(value[key])
                    page_size = daily_size if key == "daily" else size
                    value[key] = value[key][offset:offset + page_size]
                    pagination[key] = {"offset": offset, "total": total, "pageSize": page_size, "hasMore": offset + page_size < total}
            for child in value.values():
                if isinstance(child, dict):
                    page_records(child)
            if pagination:
                value["pagination"] = pagination
        page_records(result)
        return {"task": meta, "result": result}

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
                portfolio_busy = any(row["id"] in self.inflight and json.loads(row["meta"])["kind"] in ("portfolio", "comparison") for row in rows)
                for row in rows:
                    meta = json.loads(row["meta"])
                    if meta["status"] != "queued" or row["id"] in self.inflight:
                        continue
                    if portfolio_busy and meta["kind"] in ("portfolio", "comparison"):
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
            while True:
                done = runner.step(days=5)
                result = runner.result()
                result["kind"] = meta["kind"]
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
