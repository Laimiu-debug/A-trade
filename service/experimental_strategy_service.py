"""Isolated, opt-in historical experiment queue for the desktop application."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import threading
import uuid

from . import experimental_strategies as strategies


def _now():
    return datetime.now(timezone.utc).isoformat()


def _write(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    opener = gzip.open if path.name.endswith(".gz") else open
    try:
        with opener(temporary, "wt", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


class ExperimentalStrategyService:
    def __init__(self, directory, *, autostart=True, executor=None, catalog_provider=None):
        self.directory = Path(directory).resolve() / "experimental-strategies"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.executor = executor or strategies.execute_source
        self.catalog_provider = catalog_provider or strategies.catalogue
        self.lock, self.closing = threading.RLock(), threading.Event()
        self.pending, self.cancellations, self.tasks = queue.Queue(), {}, {}
        for folder in self.directory.iterdir():
            if not folder.is_dir() or not re.fullmatch(r"[0-9a-f]{32}", folder.name):
                continue
            path = folder / "task.json"
            if not path.exists():
                continue
            try:
                row = strategies._read(path)
                if row["id"] != folder.name:
                    continue
                if row["status"] in ("queued", "running", "stopping"):
                    row.update(status="interrupted", updatedAt=_now(), error="上次运行已中断；未自动续跑。请创建新实验任务。")
                    _write(path, row)
                self.tasks[row["id"]] = row
            except (ValueError, KeyError, OSError):
                # Leave damaged files untouched rather than repairing/deleting.
                continue
        self.thread = None
        if autostart:
            self.thread = threading.Thread(target=self._worker, name="experimental-strategies", daemon=True)
            self.thread.start()

    def dispatch(self, method, payload=None):
        payload = {} if payload is None else payload
        if not isinstance(payload, dict):
            raise ValueError("实验请求必须为对象")
        routes = {"experimental.catalogue": lambda: self.catalog_provider(),
            "experimental.create": lambda: self.create(payload), "experimental.list": self.list,
            "experimental.get": lambda: self.get(payload), "experimental.stop": lambda: self.stop(payload)}
        if method not in routes:
            raise ValueError("不支持的实验策略请求")
        return routes[method]()

    def _save(self, row):
        _write(self.directory / row["id"] / "task.json", row)
        self.tasks[row["id"]] = deepcopy(row)

    def _task(self, ident):
        if not isinstance(ident, str) or not re.fullmatch(r"[0-9a-f]{32}", ident) or ident not in self.tasks:
            raise ValueError("实验任务不存在")
        return deepcopy(self.tasks[ident])

    def create(self, payload):
        if set(payload) - {"strategyId", "sourceId", "requestId", "name"}:
            raise ValueError("此入口只复现冻结历史来源，不接受参数、账户或文件路径覆盖")
        request_id = payload.get("requestId")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 150:
            raise ValueError("requestId 必填且不超过 150 字")
        signature = strategies._digest(payload)
        with self.lock:
            if self.closing.is_set():
                raise ValueError("实验服务正在关闭")
            old = next((r for r in self.tasks.values() if r.get("requestId") == request_id), None)
            if old:
                if old["requestSignature"] != signature:
                    raise ValueError("同一 requestId 不能提交不同实验")
                return deepcopy(old)
            catalog = self.catalog_provider()
            selected = next((r for r in catalog["strategies"] if r["id"] == payload.get("strategyId")), None)
            source = next((r for r in selected["sources"] if r["id"] == payload.get("sourceId")), None) if selected else None
            if source is None or source.get("available") is not True:
                raise ValueError("请选择可用的已冻结历史来源")
            name = payload.get("name") or selected["name"] + " · " + source["label"]
            if not isinstance(name, str) or not name.strip() or len(name) > 150:
                raise ValueError("实验名称需要 1 至 150 字")
            ident = uuid.uuid4().hex
            row = {"id": ident, "name": name.strip(), "strategyId": selected["id"], "sourceId": source["id"],
                "source": deepcopy(source), "status": "queued", "createdAt": _now(), "updatedAt": _now(),
                "requestId": request_id, "requestSignature": signature, "progress": {"phase": "queued", "completed": 0, "total": 1},
                "resumeSupported": False, "historicalReplayOnly": True, "certified": False, "targetAchieved": False}
            (self.directory / ident).mkdir()
            self._save(row)
            self.cancellations[ident] = threading.Event()
            self.pending.put(ident)
            return deepcopy(row)

    def list(self):
        with self.lock:
            return {"tasks": sorted((deepcopy(r) for r in self.tasks.values()), key=lambda r: r["createdAt"], reverse=True)}

    def get(self, payload):
        with self.lock:
            task = self._task(payload.get("id"))
        output = {"task": task}
        if task["status"] != "completed":
            return output
        folder = self.directory / task["id"]
        output["summary"] = strategies._read(folder / "display-summary.json")
        output["resultRef"] = deepcopy(task["resultRef"])
        offset, limit = payload.get("offset", 0), payload.get("limit", 100)
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("结果分页参数无效")
        for name in ("curve", "trades"):
            rows = strategies._read(folder / (name + ".json"))
            output[name] = {"rows": rows[offset:offset + limit], "total": len(rows), "offset": offset, "limit": limit}
        return output

    def stop(self, payload):
        with self.lock:
            row = self._task(payload.get("id"))
            if row["status"] in ("queued", "running", "stopping"):
                self.cancellations.setdefault(row["id"], threading.Event()).set()
                row.update(status="stopped" if row["status"] == "queued" else "stopping", updatedAt=_now())
                self._save(row)
            return row

    def _worker(self):
        while not self.closing.is_set():
            try:
                ident = self.pending.get(timeout=.2)
            except queue.Empty:
                continue
            try:
                self._run_one(ident)
            finally:
                self.pending.task_done()

    def _run_one(self, ident):
        with self.lock:
            row = self._task(ident)
            if row["status"] != "queued":
                return
            row.update(status="running", startedAt=_now(), updatedAt=_now())
            self._save(row)
        stopped = self.cancellations.setdefault(ident, threading.Event())
        cancelled = lambda: self.closing.is_set() or stopped.is_set()

        def progress(value):
            strategies._check_stop(cancelled)
            with self.lock:
                current = self._task(ident)
                current.update(progress=deepcopy(value), updatedAt=_now())
                self._save(current)

        try:
            value = self.executor(row["strategyId"], row["sourceId"], progress=progress, cancelled=cancelled)
            strategies._check_stop(cancelled)
            folder = self.directory / ident
            _write(folder / "result.json.gz", value["result"])
            _write(folder / "checkpoint.json.gz", value["checkpoint"])
            _write(folder / "summary.json", value["summary"])
            _write(folder / "display-summary.json", strategies.display_summary(value["summary"]))
            _write(folder / "schedule.json", value["schedule"])
            _write(folder / "provenance.json", value["provenance"])
            _write(folder / "curve.json", value["result"].get("curve", []))
            _write(folder / "trades.json", value["result"].get("trades", []))
            strategies._check_stop(cancelled)
            with self.lock:
                row = self._task(ident)
                strategies._check_stop(cancelled)
                if row["status"] != "running":
                    raise strategies.ExecutionStopped("实验任务已停止。")
                row.update(status="completed", completedAt=_now(), updatedAt=_now(), progress={"phase": "completed", "completed": 1, "total": 1},
                    resultRef={"path": str(folder / "result.json.gz"), "sha256": hashlib.sha256((folder / "result.json.gz").read_bytes()).hexdigest()})
                self._save(row)
        except strategies.ExecutionStopped as error:
            with self.lock:
                row = self._task(ident)
                if row["status"] != "interrupted":
                    row.update(status="stopped", updatedAt=_now(), error=str(error))
                    self._save(row)
        except Exception as error:
            _write(self.directory / ident / "error-detail.json", {"type": type(error).__name__, "message": str(error)})
            with self.lock:
                row = self._task(ident)
                if row["status"] != "interrupted":
                    row.update(status="failed", updatedAt=_now(), error="实验执行失败；本地 error-detail.json 保留详情，未完成结果不会发布。")
                    self._save(row)

    def close(self):
        self.closing.set()
        with self.lock:
            for ident, row in list(self.tasks.items()):
                if row["status"] in ("queued", "running", "stopping"):
                    self.cancellations.setdefault(ident, threading.Event()).set()
                    row = deepcopy(row)
                    row.update(status="interrupted", updatedAt=_now(), error="应用关闭或任务中断；不会自动续跑。")
                    self._save(row)
        if self.thread is not None:
            self.thread.join(timeout=2)
