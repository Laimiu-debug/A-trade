const test = require('node:test');
const assert = require('node:assert/strict');
const { MarketService } = require('../desktop/market-service.cjs');

const tick = () => new Promise(resolve => setImmediate(resolve));
function harness() {
  const services = [];
  const router = new MarketService('isolated-test', { serviceFactory(_root, options) {
    const service = {
      options, calls: [], pending: [], stopped: false,
      request(method, payload) {
        this.calls.push({ method, payload });
        return new Promise(resolve => { this.pending.push(resolve); });
      },
      finish(data = {}) { this.pending.shift()({ ok: true, data }); },
      terminate(message) {
        this.stopped = true;
        for (const resolve of this.pending.splice(0)) resolve({ ok: false, error: message });
        return Promise.resolve();
      },
      close() { this.terminate('closed'); },
    };
    services.push(service); return service;
  } });
  return { router, services };
}

test('a long market calculation cannot block quotes or K lines; progress stays observable', async t => {
  const { router, services } = harness(); t.after(() => router.close());
  const study = router.request('market.research', { historyDays: 1000 });
  await tick();
  assert.equal(services.length, 2);
  const quotes = router.request('market.quotes', { symbols: ['sh600900'] });
  await tick();
  assert.equal(services[0].calls[0].method, 'market.quotes');
  services[0].finish({ items: [{ symbol: 'sh600900', price: 20 }] });
  assert.equal((await quotes).data.items[0].price, 20);
  services[1].options.onProgress({ stage: '计算市场风格', completed: 250, total: 1000, percent: 43.75 });
  const state = (await router.request('market.research.status')).data;
  assert.equal(state.busy, true);
  assert.deepEqual([state.jobs[0].method, state.jobs[0].completed, state.jobs[0].percent], ['market.research', 250, 43.75]);
  services[1].finish({ asOf: '2026-10-09' });
  assert.equal((await study).ok, true);
  assert.equal((await router.request('market.research.status')).data.busy, false);
});

test('cancelling one running calculation retires only its worker and continues queued observations', async t => {
  const { router, services } = harness(); t.after(() => router.close());
  const first = router.request('market.research', { historyDays: 1000 });
  const second = router.request('market.analysis.ladder', {});
  await tick();
  const id = router.status().jobs.find(job => job.method === 'market.research').id;
  assert.deepEqual((await router.request('market.research.cancel', { id })).data, { cancelled: true });
  assert.equal((await first).cancelled, true);
  await tick();
  assert.equal(services[0].stopped, false);
  assert.equal(services[1].stopped, true);
  assert.equal(services[2].calls[0].method, 'market.analysis.ladder');
  services[2].finish({ items: [] });
  assert.equal((await second).ok, true);
  assert.equal(router.status().busy, false);
});

test('queued duplicate computations are coalesced and snapshots precede queued long histories', async t => {
  const { router, services } = harness(); t.after(() => router.close());
  const first = router.request('market.research', { historyDays: 1000 });
  await tick();
  const later = router.request('market.research', { historyDays: 500, refresh: true });
  const duplicate = router.request('market.research', { refresh: true, historyDays: 500 });
  const list = router.request('market.snapshot');
  assert.equal(later, duplicate);
  assert.equal(router.status().jobs.length, 3);
  services[1].finish({ historyDays: 1000 }); await first; await tick();
  assert.equal(services[1].calls.at(-1).method, 'market.snapshot');
  services[1].finish({ items: [] }); await list; await tick();
  assert.equal(services[1].calls.at(-1).payload.historyDays, 500);
  services[1].finish({ historyDays: 500 });
  await later;
});

test('source changes cancel old work but let subsequent observations wait for the new source', async t => {
  const { router, services } = harness(); t.after(() => router.close());
  const old = router.request('market.research'); await tick();
  const configure = router.request('market.configure', { path: 'new-source' });
  const fresh = router.request('market.snapshot');
  await tick();
  assert.equal((await old).cancelled, true);
  assert.equal(services.length, 2, 'new worker must wait for configuration');
  assert.equal(services[0].calls.at(-1).method, 'market.configure');
  services[0].finish({ configured: true }); await configure; await tick();
  assert.equal(services[2].calls.at(-1).method, 'market.snapshot');
  services[2].finish({ source: 'new-source' });
  assert.equal((await fresh).data.source, 'new-source');
});

test('queued cancellation does not kill the active calculation and invalid controls are rejected', async t => {
  const { router, services } = harness(); t.after(() => router.close());
  const active = router.request('market.research'); await tick();
  const queued = router.request('market.snapshot');
  const id = router.status().jobs.find(job => job.method === 'market.snapshot').id;
  await router.request('market.research.cancel', { id });
  assert.equal((await queued).cancelled, true);
  assert.equal(services[1].stopped, false);
  for (const payload of [{}, { id: '1' }, { id: 1, anything: true }]) {
    assert.equal((await router.request('market.research.cancel', payload)).ok, false);
  }
  assert.equal((await router.request('account.save')).ok, false);
  services[1].finish(); await active;
});
