const { BusinessService, METHODS } = require('./business-service.cjs');

const CANCELLED = '已取消本次市场计算，保留上次结果。';
const SOURCE_CHANGED = '数据源已更新，请重新读取市场观察。';
const SOURCE_METHODS = new Set(['market.configure', 'market.scan', 'market.online.configure']);
const isCalculation = method => method === 'market.research' || method === 'market.snapshot' || method === 'market.finance.screen' || method.startsWith('market.analysis.');
const canonical = value => JSON.stringify(value, (_key, item) => item && typeof item === 'object' && !Array.isArray(item)
  ? Object.fromEntries(Object.keys(item).sort().map(key => [key, item[key]])) : item);

// Price reads and CPU-heavy observations use independent pipes. Calculations stay
// serial in their own process so each SQLite connection is owned by one thread.
class MarketService {
  constructor(directory, { serviceFactory = (root, options) => new BusinessService(root, options) } = {}) {
    this.directory = directory;
    this.serviceFactory = serviceFactory;
    this.interactive = serviceFactory(directory, { mode: 'market' });
    this.worker = null;
    this.jobs = new Map();
    this.queue = [];
    this.sequence = 0;
    this.active = null;
    this.draining = false;
    this.closed = false;
    this.mutations = 0;
    this.sourceBarrier = Promise.resolve();
    this.stopping = Promise.resolve();
  }
  status() {
    return { busy: this.jobs.size > 0, jobs: [...this.jobs.values()].map(job => ({
      id: job.id, method: job.method, parameters: job.payload, status: job === this.active ? 'running' : 'queued',
      startedAt: job.startedAt ? new Date(job.startedAt).toISOString() : null,
      elapsedMs: job.startedAt ? Date.now() - job.startedAt : 0,
      stage: job.stage, completed: job.completed, total: job.total, percent: job.percent,
    })) };
  }
  progress(data) {
    const job = this.active;
    if (!job || job.finished || !data || typeof data !== 'object') return;
    if (typeof data.stage === 'string') job.stage = data.stage.slice(0, 100);
    job.completed = Number.isSafeInteger(data.completed) && data.completed >= 0 ? data.completed : null;
    job.total = Number.isSafeInteger(data.total) && data.total >= 0 ? data.total : null;
    job.percent = Number.isFinite(data.percent) ? Math.max(job.percent || 0, Math.min(100, Math.max(0, data.percent))) : job.percent;
  }
  settle(job, response) {
    if (job.finished) return;
    job.finished = true;
    this.jobs.delete(job.id);
    this.queue = this.queue.filter(item => item !== job);
    job.resolve(response);
  }
  stopWorker(message) {
    const worker = this.worker;
    this.worker = null;
    if (worker) this.stopping = Promise.allSettled([this.stopping, worker.terminate(message)]).then(() => undefined);
    return this.stopping;
  }
  async cancel(id, message = CANCELLED) {
    const job = this.jobs.get(id);
    if (!job) return { cancelled: false };
    // Mark this result before terminating the pipe, whose pending request also
    // resolves. That late result must not overwrite the cancellation response.
    this.settle(job, { ok: false, error: message, cancelled: true });
    if (job === this.active) await this.stopWorker(message);
    return { cancelled: true };
  }
  async resetCalculations(message) {
    for (const job of [...this.jobs.values()]) this.settle(job, { ok: false, error: message, cancelled: true });
    await this.stopWorker(message);
  }
  async drain() {
    if (this.draining || this.closed || this.mutations) return;
    this.draining = true;
    try {
      while (this.queue.length && !this.closed && !this.mutations) {
        await this.stopping;
        if (this.closed || this.mutations) break;
        const job = this.queue.shift();
        if (!job || job.finished) continue;
        this.active = job;
        job.startedAt = Date.now(); job.stage = '正在准备行情计算';
        let worker;
        let response;
        try {
          if (!this.worker) this.worker = this.serviceFactory(this.directory, { mode: 'market', onProgress: data => this.progress(data) });
          worker = this.worker;
          response = await worker.request(job.method, job.payload, { timeoutMs: 600000 });
        }
        catch { response = { ok: false, error: '行情计算未完成，请重试。' }; }
        this.settle(job, response);
        if (this.active === job) this.active = null;
        // A timeout resolves the request but Python may still be calculating.
        // Retire that worker before accepting another calculation.
        if (worker && this.worker === worker && (worker.failure || (!response.ok && response.error?.includes('行情读取暂未完成')))) {
          await this.stopWorker('行情计算超时，请重新读取。');
        }
      }
    } finally {
      this.draining = false;
      if (this.queue.length && !this.closed && !this.mutations) queueMicrotask(() => this.drain());
    }
  }
  request(method, payload = {}) {
    if (this.closed) return Promise.resolve({ ok: false, error: '行情服务已关闭。' });
    if (!payload || typeof payload !== 'object' || Array.isArray(payload)) return Promise.resolve({ ok: false, error: '行情请求格式无效。' });
    if (method === 'market.research.status') {
      return Promise.resolve(Object.keys(payload).length ? { ok: false, error: '进度查询参数无效。' } : { ok: true, data: this.status() });
    }
    if (method === 'market.research.cancel') {
      if (Object.keys(payload).length !== 1 || !Number.isSafeInteger(payload.id) || payload.id <= 0) return Promise.resolve({ ok: false, error: '请选择要取消的市场计算。' });
      return this.cancel(payload.id).then(data => ({ ok: true, data }));
    }
    if (typeof method !== 'string' || !method.startsWith('market.') || !METHODS.has(method)) return Promise.resolve({ ok: false, error: '不支持此行情请求。' });
    if (SOURCE_METHODS.has(method)) {
      this.mutations++;
      const reset = this.resetCalculations(SOURCE_CHANGED);
      const work = this.sourceBarrier.then(async () => {
        await reset;
        return this.interactive.request(method, payload);
      });
      this.sourceBarrier = work.catch(() => undefined);
      return work.finally(() => { this.mutations--; this.drain(); });
    }
    if (!isCalculation(method)) return this.sourceBarrier.then(() => this.interactive.request(method, payload));
    let key, copy;
    try {
      key = method + '|' + canonical(payload);
      if (Buffer.byteLength(key) > 32 * 1024 * 1024) throw new Error();
      copy = JSON.parse(JSON.stringify(payload));
    } catch { return Promise.resolve({ ok: false, error: '行情请求格式无效或过大。' }); }
    const existing = [...this.jobs.values()].find(job => job.key === key);
    if (existing) return existing.promise;
    if (this.jobs.size >= 16) return Promise.resolve({ ok: false, error: '已有较多市场计算排队，请取消不需要的任务后重试。' });
    const job = { id: ++this.sequence, method, payload: copy, key, startedAt: null, stage: '等待计算', completed: null, total: null, percent: null, finished: false };
    job.promise = new Promise(resolve => { job.resolve = resolve; });
    this.jobs.set(job.id, job);
    // A current stock list takes precedence over historical observations that
    // have not started. Never interrupt an in-flight computation implicitly.
    if (method === 'market.snapshot') this.queue.unshift(job); else this.queue.push(job);
    this.drain();
    return job.promise;
  }
  close() {
    this.closed = true;
    this.resetCalculations('行情服务已关闭。');
    this.interactive.close();
  }
}

module.exports = { MarketService };
