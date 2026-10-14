"""Project settings and compact task metadata for the strategy API."""
import hashlib
import json
import re
from pathlib import Path
import sqlite3
from threading import RLock


def legacy_strategy_warning(strategy_id, version):
    if strategy_id != 'ths_force_rhythm_v1':
        return None
    match = re.match(r'^(\d+)\.(\d+)\.(\d+)', version or '')
    if not match or tuple(map(int, match.groups())) < (1, 0, 1):
        return '旧版节奏波实现存在买点错绑，请使用当前策略重新回测。'
    return None


class StrategyIntegration:
    def __init__(self, workspace, store, source):
        self.workspace = Path(workspace)
        self.store = store
        self.source = source
        self.lock = RLock()
        self.requests = 0
        self.names_path = self.workspace / 'finaltrade' / 'task-names.json'
        try:
            saved = json.loads(self.names_path.read_text(encoding='utf-8'))
            self.names = saved if isinstance(saved, dict) else {}
        except (OSError, ValueError):
            self.names = {}
        self.source_path = self.workspace / 'finaltrade' / 'market-source.json'
        self.save_source()

    def save_source(self):
        self.source_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.source_path.with_suffix('.json.tmp')
        temporary.write_text(json.dumps({'source': self.source}, ensure_ascii=False), encoding='utf-8')
        temporary.replace(self.source_path)

    def preferences(self):
        path = self.workspace / 'workspace-state.json'
        try:
            state = json.loads(path.read_text(encoding='utf-8')).get('state', {})
        except (OSError, ValueError):
            state = {}
        return {'fees': state.get('fees', {'commissionRate': 2.5, 'minCommission': 5}),
                'positionRules': state.get('positionRules', {'singleStockMax': 30, 'totalMax': 80}),
                'initialCapital': state.get('startCapital')}

    def active(self):
        if self.requests:
            return True
        for lock, workers in ((self.store._backtest_task_lock, self.store._backtest_running_worker_ids),
                              (self.store._backtest_plateau_task_lock, self.store._backtest_plateau_running_worker_ids)):
            with lock:
                if workers:
                    return True
        return any(item['status'] in {'pending', 'running', 'paused'} for item in self.tasks()['items'])

    def status(self, configured):
        available = bool(configured and Path(configured).is_dir())
        count = 0
        latest = None
        database = self.workspace / 'market.sqlite3'
        if database.exists():
            with sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True) as db:
                try:
                    count, latest = db.execute("SELECT COUNT(*), MAX(latest_date) FROM securities WHERE kind='stock'").fetchone()
                except sqlite3.OperationalError:
                    pass
        return {'configured': bool(configured), 'available': available, 'symbol_count': count,
                'latest_date': latest, 'source_changed': configured != self.source,
                'source_revision': hashlib.sha256(str(self.source).encode()).hexdigest()[:16],
                'preferences': self.preferences()}

    def tasks(self):
        catalogue = {item.strategy_id: item.name for item in self.store.list_strategies().items}
        rows = []
        for kind, lock, tasks, payloads in [
            ('backtest', self.store._backtest_task_lock, self.store._backtest_tasks, self.store._backtest_task_payloads),
            ('plateau', self.store._backtest_plateau_task_lock, self.store._backtest_plateau_tasks, self.store._backtest_plateau_task_payloads),
        ]:
            with lock:
                for task_id, task in tasks.items():
                    request = payloads.get(task_id)
                    base = getattr(request, 'base_payload', request)
                    result = task.result
                    point = getattr(result, 'recommended_point', None) or getattr(result, 'best_point', None)
                    stats = getattr(result, 'stats', None) or getattr(point, 'stats', None)
                    strategy_id = getattr(base, 'strategy_id', '')
                    date_from = getattr(base, 'date_from', '')
                    date_to = getattr(base, 'date_to', '')
                    label = '收益平原' if kind == 'plateau' else '策略回测'
                    rows.append({'id': task_id, 'kind': kind, 'status': task.status,
                                 'name': self.names.get(task_id) or f'{catalogue.get(strategy_id, "策略")} · {label}',
                                 'strategy_id': strategy_id, 'date_from': date_from, 'date_to': date_to,
                                 'created_at': task.progress.started_at, 'updated_at': task.progress.updated_at,
                                 'progress': task.progress.percent, 'message': task.progress.message,
                                 'error': task.error, 'stats': stats.model_dump(mode='json') if stats else None})
                    rows[-1]['warning'] = legacy_strategy_warning(strategy_id, getattr(result, 'strategy_version', '')) if result else None
        return {'items': sorted(rows, key=lambda item: (item['created_at'], item['id']), reverse=True)}

    def rename(self, task_id, name):
        if task_id not in {item['id'] for item in self.tasks()['items']}:
            raise ValueError('任务不存在。')
        if not isinstance(name, str) or not name.strip() or len(name) > 120:
            raise ValueError('请填写 1～120 字的任务名称。')
        with self.lock:
            self.names[task_id] = name.strip()
            self.names_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.names_path.with_suffix('.json.tmp')
            temporary.write_text(json.dumps(self.names, ensure_ascii=False), encoding='utf-8')
            temporary.replace(self.names_path)
        return {'name': name.strip(), 'id': task_id}
