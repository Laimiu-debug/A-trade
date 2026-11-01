"""One opt-in current scan at a time, with durable results and cancellation."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import queue
import re
import threading
import uuid

from .experimental_strategies import ExecutionStopped, _read
from .experimental_strategy_service import _write
from .experimental_scan import run_current_scan, scan_clock
from .trading_calendar import trading_sessions


def _now():
    return datetime.now(timezone.utc).isoformat()


class ExperimentalScanService:
    def __init__(self, directory, *, autostart=True, executor=None):
        self.workspace = Path(directory).resolve()
        self.directory = self.workspace / "experimental-scans"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.executor = executor or run_current_scan
        self.lock, self.closing = threading.RLock(), threading.Event()
        self.pending, self.tasks, self.cancellations = queue.Queue(), {}, {}
        for folder in self.directory.iterdir():
            if not folder.is_dir() or not re.fullmatch(r"[0-9a-f]{32}", folder.name):
                continue
            try:
                row = _read(folder / "task.json")
                if row["id"] != folder.name or row["kind"] != "currentScan":
                    continue
                if row["status"] in ("queued", "running", "stopping"):
                    row.update(status="interrupted", updatedAt=_now(), error="上次扫描已中断；请重新扫描。")
                    _write(folder / "task.json", row)
                self.tasks[row["id"]] = row
            except (ValueError, KeyError, OSError):
                continue
        self.thread = None
        if autostart:
            self.thread = threading.Thread(target=self._worker, daemon=True, name="experimental-current-scan")
            self.thread.start()

    def _save(self, row):
        _write(self.directory / row["id"] / "task.json", row)
        self.tasks[row["id"]] = deepcopy(row)

    def _task(self, ident):
        if not isinstance(ident, str) or not re.fullmatch(r"[0-9a-f]{32}", ident) or ident not in self.tasks:
            raise ValueError("扫描任务不存在")
        return deepcopy(self.tasks[ident])

    def dispatch(self, method, payload=None):
        payload = {} if payload is None else payload
        if not isinstance(payload, dict):
            raise ValueError("扫描请求必须为对象")
        routes = {"create": self.create, "list": self.list, "get": self.get, "stop": self.stop}
        action = method.removeprefix("research.experimental.scan.")
        if action not in routes:
            raise ValueError("不支持的当前扫描请求")
        return routes[action](payload)

    def create(self, payload):
        request = payload.get("requestId")
        if set(payload) != {"requestId"} or not isinstance(request, str) or not 1 <= len(request) <= 150:
            raise ValueError("扫描仅接受 requestId，使用当前已配置行情与固定策略规则")
        with self.lock:
            if self.closing.is_set():
                raise ValueError("扫描服务正在关闭")
            old = next((r for r in self.tasks.values() if r["requestId"] == request), None)
            if old:
                return deepcopy(old)
            active = next((r for r in self.tasks.values() if r["status"] in ("queued", "running", "stopping")), None)
            if active:
                return deepcopy(active)
            ident = uuid.uuid4().hex
            row = {"id": ident, "kind": "currentScan", "name": "当前环境与三策略选股", "status": "queued",
                "requestId": request, "createdAt": _now(), "updatedAt": _now(),
                "progress": {"phase": "等待扫描", "completed": 0, "total": 1}}
            (self.directory / ident).mkdir()
            self._save(row)
            self.cancellations[ident] = threading.Event()
            self.pending.put(ident)
            return deepcopy(row)

    def list(self, payload=None):
        if payload:
            raise ValueError("扫描列表不接受额外参数")
        with self.lock:
            return {"tasks": sorted((deepcopy(r) for r in self.tasks.values()), key=lambda r: r["createdAt"], reverse=True)}

    def get(self, payload):
        if set(payload) != {"id"}:
            raise ValueError("扫描详情只接受任务 id")
        with self.lock:
            row = self._task(payload["id"])
        result = {"task": row}
        if row["status"] == "completed":
            result["result"] = _read(self.directory / row["id"] / "result.json")
            try:
                clock = scan_clock()
                as_of = result["result"]["asOfDate"]
                expected = clock["expectedAsOfDate"]
                sessions = trading_sessions(as_of, expected)
                if sessions is None or not sessions or as_of > expected:
                    raise ValueError("Cannot establish saved-result freshness")
                result["freshness"] = {"checkedAt": clock["requestedAt"], "expectedAsOfDate": expected,
                    "stale": as_of < expected, "lagSessions": len(sessions) - 1, "available": True}
            except (ValueError, KeyError, TypeError):
                result["freshness"] = {"checkedAt": _now(), "expectedAsOfDate": None, "stale": None,
                    "lagSessions": None, "available": False, "reason": "当前日历无法核验"}
        return result

    def stop(self, payload):
        if set(payload) != {"id"}:
            raise ValueError("停止扫描只接受任务 id")
        with self.lock:
            row = self._task(payload["id"])
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
                self._run(ident)
            finally:
                self.pending.task_done()

    def _run(self, ident):
        cancelled = lambda: self.closing.is_set() or self.cancellations[ident].is_set()
        with self.lock:
            row = self._task(ident)
            if cancelled() or row["status"] != "queued":
                return
            row.update(status="running", updatedAt=_now())
            self._save(row)
        def progress(value):
            if cancelled():
                raise ExecutionStopped("扫描已停止")
            with self.lock:
                row = self._task(ident)
                if row["status"] != "running":
                    raise ExecutionStopped("扫描已停止")
                row.update(progress=deepcopy(value), updatedAt=_now())
                self._save(row)
        try:
            result = self.executor(self.workspace, progress=progress, cancelled=cancelled)
            with self.lock:
                row = self._task(ident)
                if cancelled() or row["status"] != "running":
                    raise ExecutionStopped("扫描已停止")
                _write(self.directory / ident / "result.json", result)
                row.update(status="completed", updatedAt=_now(), completedAt=_now(),
                    progress={"phase": "扫描完成", "completed": 1, "total": 1})
                self._save(row)
        except ExecutionStopped:
            with self.lock:
                row = self._task(ident)
                if row["status"] != "interrupted":
                    row.update(status="stopped", updatedAt=_now())
                    self._save(row)
        except Exception as error:
            with self.lock:
                row = self._task(ident)
                if row["status"] == "interrupted":
                    return
                detail = str(error)
                _write(self.directory / ident / "error-detail.json", {"type": type(error).__name__, "message": detail})
                public = detail if isinstance(error, ValueError) and not re.search(r"[A-Za-z]:[/\\]|Traceback", detail) else "扫描失败，请检查本地行情并重试；诊断已保存在扫描记录。"
                row.update(status="failed", updatedAt=_now(), error=public[:400])
                self._save(row)

    def close(self):
        self.closing.set()
        with self.lock:
            for row in list(self.tasks.values()):
                if row["status"] in ("queued", "running", "stopping"):
                    self.cancellations.setdefault(row["id"], threading.Event()).set()
                    row = deepcopy(row)
                    row.update(status="interrupted", updatedAt=_now(), error="应用关闭，扫描已中断；不会自动续跑。")
                    self._save(row)
        if self.thread:
            self.thread.join(timeout=2)
