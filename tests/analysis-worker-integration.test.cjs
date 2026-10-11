const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { BusinessService } = require('../desktop/business-service.cjs');
const { MarketService } = require('../desktop/market-service.cjs');

async function until(check, message, timeout = 15000) {
  const started = Date.now();
  while (Date.now() - started < timeout) {
    if (check()) return;
    await new Promise(resolve => setTimeout(resolve, 20));
  }
  assert.fail(message);
}

function fixtureBars(count) {
  const bytes = Buffer.alloc(count * 32);
  const day = new Date('2020-01-02T00:00:00Z');
  for (let index = 0; index < count; day.setUTCDate(day.getUTCDate() + 1)) {
    if ([0, 6].includes(day.getUTCDay())) continue;
    const offset = index * 32, cents = 1000 + index;
    bytes.writeUInt32LE(Number(day.toISOString().slice(0, 10).replaceAll('-', '')), offset);
    for (const field of [4, 8, 12, 16]) bytes.writeUInt32LE(cents, offset + field);
    bytes.writeFloatLE(cents * 1000, offset + 20);
    bytes.writeUInt32LE(100000, offset + 24);
    index++;
  }
  return bytes;
}

test('real analysis worker cancels independently, preserves watchlist writes, and restarts cleanly', { timeout: 60000 }, async t => {
  const temporary = await fs.mkdtemp(path.join(os.tmpdir(), 'atrade-worker-integration-'));
  const root = path.resolve(temporary);
  const dayDirectory = path.join(root, 'tdx', 'vipdoc', 'sh', 'lday');
  const services = [];
  const router = new MarketService(path.join(root, 'state'), {
    serviceFactory(directory, options) {
      const service = new BusinessService(directory, options);
      services.push(service);
      return service;
    },
  });
  t.after(async () => {
    router.close();
    await Promise.allSettled(services.map(service => service.terminate('integration test cleanup')));
    await until(() => services.every(service => service.child.exitCode !== null || service.child.signalCode !== null), 'test workers must exit');
    // Only this test's verified, freshly created temporary directory is removed.
    assert.equal(path.dirname(root), path.resolve(os.tmpdir()));
    assert.match(path.basename(root), /^atrade-worker-integration-/);
    await fs.rm(root, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
  });
  await fs.mkdir(dayDirectory, { recursive: true });
  const bytes = fixtureBars(1600);
  // A sufficiently large real local workload exposes accidental pipe sharing,
  // without adding sleeps, hooks, or public network dependencies to Python.
  for (let start = 1; start <= 800; start += 40) {
    await Promise.all(Array.from({ length: 40 }, (_, offset) => {
      const code = String(600000 + start + offset);
      return fs.writeFile(path.join(dayDirectory, `sh${code}.day`), bytes);
    }));
  }
  const configured = await router.request('market.configure', { path: path.join(root, 'tdx') });
  assert.equal(configured.ok, true, configured.error);
  const scanned = await router.request('market.scan');
  assert.equal(scanned.ok, true, scanned.error);
  assert.equal(scanned.data.symbolCount, 800);

  let settled = false;
  const calculation = router.request('market.analysis.ladder', { historyDays: 1000 }).then(value => { settled = true; return value; });
  await until(() => router.status().jobs.some(job => job.status === 'running' && job.stage !== '正在准备行情计算'), 'real worker must report Python progress');
  const firstWorker = router.worker;
  assert.ok(firstWorker?.child.pid);
  assert.notEqual(firstWorker.child.pid, router.interactive.child.pid);

  const saved = await router.request('market.watchlist.save', { symbol: 'sh600001', note: 'saved during calculation', tags: ['integration'] });
  assert.equal(saved.ok, true, saved.error);
  const beforeCancel = await router.request('market.watchlist.list');
  assert.equal(beforeCancel.ok, true, beforeCancel.error);
  assert.equal(beforeCancel.data.items[0].note, 'saved during calculation');
  assert.equal(settled, false, 'interactive writes must complete while the heavy calculation is still running');
  const job = router.status().jobs[0];
  assert.ok(job);
  const cancelled = await router.request('market.research.cancel', { id: job.id });
  assert.equal(cancelled.ok, true, cancelled.error);
  assert.equal(cancelled.data.cancelled, true);
  assert.equal((await calculation).cancelled, true);
  await until(() => firstWorker.child.exitCode !== null || firstWorker.child.signalCode !== null, 'cancelled worker must exit');

  const next = await router.request('market.analysis.trends', { minReturn20: 0 });
  assert.equal(next.ok, true, next.error);
  assert.notEqual(router.worker.child.pid, firstWorker.child.pid);
  assert.equal(router.status().jobs.length, 0);
  const afterRestart = await router.request('market.watchlist.list');
  assert.equal(afterRestart.ok, true, afterRestart.error);
  assert.deepEqual(afterRestart.data.items, beforeCancel.data.items);
});
