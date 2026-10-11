const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { BusinessService } = require('../desktop/business-service.cjs');
const { MarketService } = require('../desktop/market-service.cjs');

test('real Python pipes report progress, recover after cancellation and preserve source isolation', async t => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'atrade-market-pipes-'));
  const root = path.join(directory, 'tdx');
  const folder = path.join(root, 'vipdoc/sh/lday');
  await fs.mkdir(folder, { recursive: true });
  const dates = [];
  for (let day = new Date('2025-01-01T00:00:00Z'); dates.length < 120; day.setUTCDate(day.getUTCDate() + 1)) {
    if (day.getUTCDay() !== 0 && day.getUTCDay() !== 6) dates.push(Number(day.toISOString().slice(0, 10).replaceAll('-', '')));
  }
  for (let stock = 0; stock < 64; stock++) {
    const bytes = Buffer.alloc(dates.length * 32);
    dates.forEach((date, i) => {
      const offset = i * 32, close = 1000 + i + stock;
      bytes.writeUInt32LE(date, offset);
      for (const [position, value] of [[4, close - 2], [8, close + 5], [12, close - 5], [16, close]]) bytes.writeUInt32LE(value, offset + position);
      bytes.writeFloatLE(1000000 + i * 1000, offset + 20);
      bytes.writeUInt32LE(100000 + i * 100, offset + 24);
    });
    await fs.writeFile(path.join(folder, `sh${600100 + stock}.day`), bytes);
  }
  const services = [], progress = [];
  const router = new MarketService(path.join(directory, 'workspace'), { serviceFactory(rootPath, options) {
    const service = new BusinessService(rootPath, { ...options, onProgress(data, ...rest) {
      progress.push(data); options.onProgress?.(data, ...rest);
    } });
    services.push(service); return service;
  } });
  t.after(async () => {
    router.close();
    await Promise.all(services.map(service => service.terminate('test complete')));
    // Generated fixture only; never recurse outside its verified temporary root.
    assert.ok(path.resolve(directory).startsWith(path.resolve(os.tmpdir()) + path.sep));
    await fs.rm(directory, { recursive: true, force: true });
  });
  assert.equal((await router.request('market.configure', { path: root })).ok, true);
  assert.equal((await router.request('market.scan')).ok, true);
  const list = router.request('market.snapshot');
  assert.equal(router.status().busy, true);
  const bars = await router.request('market.bars', { symbol: 'sh600100', limit: 60 });
  assert.equal(bars.ok, true, bars.error);
  assert.equal(bars.data.rows.length, 60);
  const snapshot = await list;
  assert.equal(snapshot.ok, true, snapshot.error);
  assert.equal(snapshot.data.items.length, 64);
  assert.equal(snapshot.data.timeline, undefined, 'stock lists do not require full style histories');
  assert.notEqual(services[0].child.pid, services[1].child.pid);
  assert.ok(progress.some(item => item.stage === '读取日线' && item.total === 64));
  assert.ok(progress.some(item => item.percent === 100));
  const study = router.request('market.research', { historyDays: 1000, refresh: true });
  const job = router.status().jobs.find(item => item.method === 'market.research');
  assert.equal((await router.request('market.research.cancel', { id: job.id })).data.cancelled, true);
  assert.equal((await study).cancelled, true);
  const resumed = await router.request('market.snapshot', { refresh: true });
  assert.equal(resumed.ok, true, resumed.error);
  assert.equal(resumed.data.items.length, 64);
  const otherRoot = path.join(directory, 'empty-tdx');
  await fs.mkdir(path.join(otherRoot, 'vipdoc/sh/lday'), { recursive: true });
  assert.equal((await router.request('market.configure', { path: otherRoot })).ok, true);
  assert.equal((await router.request('market.scan')).ok, true);
  const other = await router.request('market.snapshot');
  assert.equal(other.ok, true, other.error);
  assert.equal(other.data.items.length, 0, 'a restarted compute worker must not reuse the previous directory');
});
