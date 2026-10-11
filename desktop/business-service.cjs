const { spawn } = require('node:child_process');
const path = require('node:path');
const fs = require('node:fs');

const METHODS = new Set(['workspace', 'account.save', 'account.archive', 'account.asof', 'ledger.save', 'ledger.void',
  'research.catalogue', 'research.create', 'research.list', 'research.get', 'research.control', 'research.replay',
  'import.preview', 'import.commit', 'review.save', 'review.delete', 'review.score', 'plan.save', 'plan.delete',
  'snapshot.save', 'backup.export', 'backup.restore',
  'market.status', 'market.configure', 'market.scan', 'market.search', 'market.bars',
  'market.online.status', 'market.online.configure', 'market.online.test', 'market.quotes',
  'market.intraday', 'market.sectors', 'market.sector', 'market.research', 'market.snapshot',
  'market.news.list', 'market.finance.snapshot', 'market.finance.screen', 'market.funds.sectors',
  'market.finance.benchmark', 'market.finance.context', 'market.funds.history', 'market.sector.history',
  'market.funds.activity.list', 'market.funds.activity.history', 'market.regulatory.list',
  'market.analysis.overview', 'market.analysis.trends', 'market.analysis.moves', 'market.analysis.rebounds', 'market.analysis.ladder',
  'market.watchlist.list', 'market.watchlist.save', 'market.watchlist.delete',
  'market.watchlist.save-many', 'market.watchlist.export', 'market.watchlist.import',
  'market.watchlist.backup.export', 'market.watchlist.backup.preview', 'market.watchlist.backup.restore']);
const MAX_BYTES = 32 * 1024 * 1024;

function pythonCommand() {
  if (process.env.ATRADE_PYTHON) return process.env.ATRADE_PYTHON;
  const local = path.join(__dirname, '..', '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
  if (fs.existsSync(local)) return local;
  const root = path.join(process.env.LOCALAPPDATA || '', 'Programs', 'Python');
  if (fs.existsSync(root)) {
    const versions = fs.readdirSync(root).filter(name => /^Python3\d+$/.test(name)).sort((a, b) => b.localeCompare(a, undefined, { numeric: true }));
    for (const version of versions) {
      const candidate = path.join(root, version, 'python.exe');
      if (fs.existsSync(candidate)) return candidate;
    }
  }
  return process.platform === 'win32' ? 'python' : 'python3';
}

class BusinessService {
  constructor(directory, { mode = 'all', onProgress } = {}) {
    this.pending = new Map();
    this.sequence = 0;
    this.closed = false;
    this.buffer = '';
    this.onProgress = onProgress;
    this.child = spawn(pythonCommand(), ['-B', '-u', path.join(__dirname, '../service/entry.py'), directory, mode], {
      windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'],
      env: { ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1' },
    });
    this.child.stdout.setEncoding('utf8');
    this.child.stdout.on('data', chunk => this.receive(chunk));
    this.child.stderr.on('data', () => {}); // Private pipe; no imported data or traceback in logs.
    this.child.on('error', () => this.fail('账户服务启动失败。请安装 Python 3.11 或以上版本后重启。'));
    this.child.on('exit', () => this.fail('账户服务已停止，请重启软件后重试。未完成的操作请先核对账本。'));
    this.child.stdin.on('error', () => this.fail('账户服务连接中断，请重启软件。'));
  }
  fail(message) {
    this.failure = message;
    for (const { resolve, timeout } of this.pending.values()) {
      clearTimeout(timeout);
      resolve({ ok: false, error: message });
    }
    this.pending.clear();
  }
  receive(chunk) {
    this.buffer += chunk;
    if (Buffer.byteLength(this.buffer) > MAX_BYTES * 2) {
      this.fail('账户数据过大，读取已停止。'); this.child.kill(); return;
    }
    let end;
    while ((end = this.buffer.indexOf('\n')) >= 0) {
      const line = this.buffer.slice(0, end); this.buffer = this.buffer.slice(end + 1);
      try {
        const response = JSON.parse(line);
        const item = this.pending.get(response.id);
        if (item) {
          if (response.event === 'progress') {
            this.onProgress?.(response.data, item.method, response.id);
            continue;
          }
          clearTimeout(item.timeout); this.pending.delete(response.id);
          item.resolve(response.ok ? { ok: true, data: response.data } : { ok: false, error: response.error || '业务操作失败。' });
        }
      } catch { this.fail('账户服务返回无效数据，请重启软件。'); }
    }
  }
  request(method, payload = {}, { timeoutMs } = {}) {
    if (!METHODS.has(method) || !payload || typeof payload !== 'object' || Array.isArray(payload)) return Promise.resolve({ ok: false, error: '不支持此业务请求。' });
    if (this.failure || this.closed) return Promise.resolve({ ok: false, error: this.failure || '账户服务已关闭。' });
    const id = ++this.sequence;
    let line;
    try { line = JSON.stringify({ id, method, payload }) + '\n'; } catch { return Promise.resolve({ ok: false, error: '请求格式无效。' }); }
    if (Buffer.byteLength(line) > MAX_BYTES) return Promise.resolve({ ok: false, error: '文件过大，请分批导入。' });
    return new Promise(resolve => {
      const timeout = setTimeout(() => {
        this.pending.delete(id);
        resolve({ ok: false, error: method.startsWith('market.')
          ? '行情读取暂未完成，请检查目录是否可访问，稍后重新读取。'
          : '本地操作仍未返回，请重新打开账户核对后再操作，避免重复记账。' });
      }, timeoutMs ?? (method.startsWith('market.') ? 120000 : 60000));
      this.pending.set(id, { resolve, timeout, method });
      this.child.stdin.write(line, 'utf8');
    });
  }
  close() {
    this.closed = true;
    this.child.stdin.end();
    const timer = setTimeout(() => this.child.kill(), 3000);
    timer.unref();
    this.child.once('exit', () => clearTimeout(timer));
  }
  terminate(message = '已取消本次市场计算，保留上次结果。') {
    this.closed = true;
    this.fail(message);
    if (this.child.exitCode !== null || this.child.signalCode !== null) return Promise.resolve();
    return new Promise(resolve => {
      const timer = setTimeout(resolve, 3000);
      timer.unref();
      this.child.once('exit', () => { clearTimeout(timer); resolve(); });
      this.child.stdin.destroy();
      this.child.kill();
    });
  }
}

module.exports = { BusinessService, pythonCommand, METHODS };
