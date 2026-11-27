const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const http = require('node:http');
const crypto = require('node:crypto');
const vm = require('node:vm');
const { AIService } = require('../desktop/ai-service.cjs');
const { BusinessService } = require('../desktop/business-service.cjs');
const { MarketService } = require('../desktop/market-service.cjs');
const { summarizeSelfAccount } = require('../desktop/master-self-compare.cjs');

const key = crypto.randomBytes(32);
const secure = {
  isEncryptionAvailable: () => true,
  encryptString(value) {
    const iv = crypto.randomBytes(12);
    const cipher = crypto.createCipheriv('aes-256-gcm', key, iv);
    const enc = Buffer.concat([cipher.update(value), cipher.final()]);
    return Buffer.concat([iv, cipher.getAuthTag(), enc]);
  },
  decryptString(value) {
    const cipher = crypto.createDecipheriv('aes-256-gcm', key, value.subarray(0, 12));
    cipher.setAuthTag(value.subarray(12, 28));
    return Buffer.concat([cipher.update(value.subarray(28)), cipher.final()]).toString();
  },
};

const png = {
  name: 'slip.png',
  mimeType: 'image/png',
  dataBase64: 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aWQAAAABJRU5ErkJggg==',
};

async function setupAI(t, responder) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'atrade-master-ai-'));
  const received = [];
  const server = http.createServer(async (req, res) => {
    let body = '';
    for await (const chunk of req) body += chunk;
    received.push({ body: JSON.parse(body), url: req.url });
    responder(res, received.at(-1));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const baseUrl = `http://127.0.0.1:${server.address().port}/v1`;
  const ai = new AIService(dir, secure);
  ai.save({ target: 'text', baseUrl, apiKey: 'secret-text-key', model: 'text-coach', enabled: true });
  ai.save({ target: 'vision', baseUrl, apiKey: 'secret-vision-key', model: 'vision-ocr', enabled: true });
  t.after(() => {
    ai.close();
    server.closeAllConnections();
    server.close();
    fs.rmSync(dir, { recursive: true, force: true });
  });
  return { ai, received };
}

test('master.recognize extracts up to 8 delivery slip screenshots including name-only rows and equity hints', async t => {
  const { ai, received } = await setupAI(t, res => {
    res.setHeader('Content-Type', 'application/json');
    res.end(JSON.stringify({
      choices: [{
        message: {
          content: JSON.stringify({
            rows: [
              { type: 'buy', date: '2025-10-10', time: '09:35:00', code: null, name: '推土机龙头', quantity: 2000, price: 12.5, amount: 25000, accountEquity: 500000, positionRatio: 5.0 },
              { type: 'sell', date: null, time: '10:05:00', code: '600101', name: '推土机龙头', quantity: 2000, price: 13.8 },
            ],
            warnings: [],
          }),
        },
      }],
    }));
  });

  const batch = Array.from({ length: 6 }, (_, i) => ({ ...png, name: `slip-${i + 1}.png` }));
  const res = await ai.dispatch('master.recognize', { images: batch, defaultDate: '2025-10-11', defaultYear: '2025' });
  assert.equal(res.ok, true, res.error);
  assert.equal(res.data.imageCount, 6);
  assert.equal(res.data.rows.length, 2);
  assert.equal(received[0].body.model, 'vision-ocr');
  assert.equal(received[0].body.store, false);
  const nameOnly = res.data.rows.find(r => r.code === null);
  assert.equal(nameOnly.name, '推土机龙头');
  assert.equal(nameOnly.accountEquity, 500000);
  const filledDate = res.data.rows.find(r => r.code === '600101');
  assert.equal(filledDate.date, '2025-10-11');
  assert.ok(filledDate.importKey.startsWith('master-ocr:'));

  // Rejects >8 images cleanly
  const tooMany = await ai.dispatch('master.recognize', { images: Array(9).fill(png) });
  assert.equal(tooMany.ok, false);
});

test('master.study generates structured coach report and redacts echoed keys', async t => {
  const { ai, received } = await setupAI(t, res => {
    res.setHeader('Content-Type', 'application/json');
    res.end(JSON.stringify({
      choices: [{
        message: {
          content: '## 核心解构\n- 胜率 66.7%，亏损单次日果断止损。\n- 误回显测试 secret-text-key',
        },
      }],
    }));
  });

  const res = await ai.dispatch('master.study', {
    kind: 'non_realtime_plan',
    masterName: '推土机大佬',
    styleTag: '连板+趋势',
    analysis: { summary: { winRatePct: 66.7, payoffRatio: 2.4 } },
  });
  assert.equal(res.ok, true, res.error);
  assert.equal(received[0].body.model, 'text-coach');
  assert.match(res.data.title, /非盯盘可执行改造方案/);
  assert.ok(!res.data.content.includes('secret-text-key'));
  assert.ok(res.data.content.includes('[已移除密钥]'));
});

test('MarketService routes master analysis to compute worker and CRUD to interactive pipe', async t => {
  const services = [];
  const router = new MarketService('isolated', {
    serviceFactory(_root, _options) {
      const service = {
        calls: [],
        pending: [],
        request(method, payload, settings) {
          this.calls.push({ method, payload, settings });
          return new Promise(resolve => this.pending.push(resolve));
        },
        terminate(msg) { this.pending.splice(0).forEach(r => r({ ok: false, error: msg })); return Promise.resolve(); },
        close() { this.terminate('closed'); },
      };
      services.push(service);
      return service;
    },
  });
  t.after(() => router.close());

  const analyzePromise = router.request('market.masters.analyze', { masterId: 'm1' });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(services[1].calls[0].method, 'market.masters.analyze');

  const listPromise = router.request('market.masters.list', {});
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(services[0].calls[0].method, 'market.masters.list');
  services[0].pending.shift()({ ok: true, data: { items: [] } });
  assert.deepEqual((await listPromise).data.items, []);

  services[1].pending.shift()({ ok: true, data: { empty: true } });
  assert.equal((await analyzePromise).data.empty, true);
});

test('summarizeSelfAccount computes anonymized discipline metrics without leaking private notes', () => {
  const summary = summarizeSelfAccount({
    accountId: 'acc-secret-id',
    accounts: [{ id: 'acc-secret-id', name: '我的主账户' }],
    ledger: [
      { type: 'buy', date: '2025-10-08', code: '600900', quantity: 1000, price: 20, amount: 20000, fees: 5, reason: '私密理由' },
      { type: 'sell', date: '2025-10-10', code: '600900', quantity: 1000, price: 22, amount: 22000, fees: 25 },
      { type: 'buy', date: '2025-10-13', code: '000001', quantity: 1000, price: 10, amount: 10000, fees: 5 },
      { type: 'sell', date: '2025-10-20', code: '000001', quantity: 1000, price: 9.5, amount: 9500, fees: 15 },
    ],
  });
  assert.equal(summary.accountName, '我的主账户');
  assert.equal(summary.tradeCount, 4);
  assert.equal(summary.closedCount, 2);
  assert.equal(summary.winRatePct, 50);
  assert.ok(summary.payoffRatio > 1.5);
  assert.equal(summary.avgHoldDaysWin, 2);
  assert.equal(summary.avgHoldDaysLoss, 7);
  assert.ok(!JSON.stringify(summary).includes('acc-secret-id'));
  assert.ok(!JSON.stringify(summary).includes('私密理由'));
});

test('real Python pipe persists master database across restart and drives T11 UI rendering cleanly', async t => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'atrade-master-pipe-'));
  const tdxRoot = path.join(dir, 'tdx');
  const folder = path.join(tdxRoot, 'vipdoc/sh/lday');
  fs.mkdirSync(folder, { recursive: true });
  const dates = [];
  for (let d = new Date('2025-07-01T00:00:00Z'); dates.length < 45; d.setUTCDate(d.getUTCDate() + 1)) {
    if (d.getUTCDay() !== 0 && d.getUTCDay() !== 6) dates.push(d.toISOString().slice(0, 10));
  }
  const writeStock = (sym, basePx, jumpIdx) => {
    const buf = Buffer.alloc(dates.length * 32);
    let px = basePx;
    dates.forEach((iso, i) => {
      px = i === jumpIdx ? px * 1.10 : px * 1.004;
      const close = Math.round(px * 100);
      const off = i * 32;
      buf.writeUInt32LE(Number(iso.replaceAll('-', '')), off);
      buf.writeUInt32LE(Math.max(1, close - 5), off + 4);
      buf.writeUInt32LE(close + 8, off + 8);
      buf.writeUInt32LE(Math.max(1, close - 8), off + 12);
      buf.writeUInt32LE(close, off + 16);
      buf.writeFloatLE(5e8 + i * 1e7, off + 20);
      buf.writeUInt32LE(400000 + i * 1000, off + 24);
    });
    fs.writeFileSync(path.join(folder, `${sym}.day`), buf);
  };
  for (let k = 0; k < 24; k++) writeStock(`sh${600100 + k}`, 10 + (k % 4), 28);

  const wsDir = path.join(dir, 'workspace');
  const spawned = [];
  const makeRouter = () => new MarketService(wsDir, {
    serviceFactory(rootPath, options) {
      const svc = new BusinessService(rootPath, options);
      spawned.push(svc);
      return svc;
    },
  });
  let router = makeRouter();
  t.after(async () => {
    router?.close();
    await Promise.allSettled(spawned.map(s => s.terminate('test complete')));
    await fs.promises.rm(dir, { recursive: true, force: true });
  });

  assert.equal((await router.request('market.configure', { path: tdxRoot })).ok, true);
  assert.equal((await router.request('market.scan')).ok, true);
  const created = await router.request('market.masters.save', { name: '推土机管道测试', styleTag: '连板', initialCapital: 500000 });
  assert.equal(created.ok, true, created.error);
  const masterId = created.data.master.id;

  const commit = await router.request('market.masters.import.commit', {
    masterId,
    source: 'ocr',
    rows: [
      { tradeDate: dates[28], tradeTime: '09:36:00', side: 'buy', code: '600100', name: '样本龙头', quantity: 5000, price: 12.1 },
      { tradeDate: dates[30], tradeTime: '10:20:00', side: 'sell', code: '600100', name: '样本龙头', quantity: 5000, price: 13.3 },
    ],
  });
  assert.equal(commit.ok, true, commit.error);
  assert.equal(commit.data.inserted, 2);

  // Restart MarketService and verify persistence + full analysis over real pipe
  router.close();
  await Promise.allSettled(spawned.map(s => s.terminate('restart')));
  router = makeRouter();
  const listed = await router.request('market.masters.list');
  assert.equal(listed.ok, true);
  assert.equal(listed.data.items.length, 1);
  assert.equal(listed.data.items[0].stats.tradeCount, 2);

  const analyzed = await router.request('market.masters.analyze', { masterId });
  assert.equal(analyzed.ok, true, analyzed.error);
  assert.equal(analyzed.data.summary.closedEpisodes, 1);
  assert.equal(analyzed.data.coverage.tradesWithDailyBar, 2);

  // Load frontend scripts in Node VM wired to the real MarketService pipe
  const listeners = new Map();
  let rendered = 0;
  const ctx = {
    console, setTimeout, clearTimeout, queueMicrotask, Promise, URL, Blob,
    Date, Math, Number, String, Array, Object, Set, Map, JSON, RegExp, Error,
    structuredClone,
    document: {
      addEventListener(type, fn) { listeners.set(type, fn); },
      querySelector: () => null,
      createElement: () => ({ click() {}, remove() {} }),
      body: { appendChild() {} },
    },
    window: null,
    localStorage: { getItem: () => null, setItem() {} },
  };
  ctx.window = ctx;
  ctx.atradeDesktop = {
    bootstrap: { state: { version: 1, page: 'T11' }, storage: {}, business: {}, ai: {}, market: {}, app: { version: '0.4.9' } },
    market: (method, payload) => router.request(method, payload),
    ai: () => Promise.resolve({ ok: false, error: 'unused' }),
  };
  vm.createContext(ctx);
  for (const file of ['icons.js', 'data.js', 'views.js', 'master-slips.js']) {
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../preview', file), 'utf8'), ctx, { filename: file });
  }
  assert.ok(ctx.AT.pages.T11, 'T11 page registered in AT.pages');
  ctx.AT.state.page = 'T11';
  ctx.AT.render = () => { rendered += 1; };
  ctx.AT.view('T11');
  // Wait for async microtasks + real Python pipe responses to hydrate state
  for (let i = 0; i < 40; i++) {
    await new Promise(resolve => setTimeout(resolve, 40));
    if (ctx.AT.masterSlips.getAIContext().evidence?.summary?.closedEpisodes === 1) break;
  }
  const html = ctx.AT.view('T11');
  assert.ok(rendered > 0);
  assert.ok(html.includes('推土机管道测试'), 'renders master card');
  assert.ok(html.includes('ms-chart-svg'), 'renders SVG equity & position chart');
  const aiCtx = ctx.AT.masterSlips.getAIContext();
  assert.equal(aiCtx.pageId, 'T11');
  assert.equal(aiCtx.evidence.summary.closedEpisodes, 1);
  assert.equal(await ctx.AT.masterSlips.flush(), true);
});
