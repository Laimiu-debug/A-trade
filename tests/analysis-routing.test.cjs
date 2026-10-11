const test = require('node:test');
const assert = require('node:assert/strict');
const { MarketService } = require('../desktop/market-service.cjs');

test('complete financial screening uses the disposable worker while watchlist saves remain interactive', async t => {
  const services = [];
  const router = new MarketService('isolated', { serviceFactory(root, options) {
    const service = { calls: [], pending: [], request(method, payload, settings) {
      this.calls.push({ method, payload, settings });
      return new Promise(resolve => this.pending.push(resolve));
    }, terminate(message) { this.pending.splice(0).forEach(resolve => resolve({ ok: false, error: message })); return Promise.resolve(); }, close() { this.terminate('closed'); } };
    services.push(service); return service;
  } });
  t.after(() => router.close());
  const screen = router.request('market.finance.screen', { positive: true });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(services[1].calls[0].method, 'market.finance.screen');
  assert.equal(services[1].calls[0].settings.timeoutMs, 600000);
  const saved = router.request('market.watchlist.save-many', { items: [{ symbol: 'sh600900' }] });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(services[0].calls[0].method, 'market.watchlist.save-many');
  services[0].pending.shift()({ ok: true, data: { added: 1 } });
  assert.equal((await saved).data.added, 1);
  const id = router.status().jobs[0].id;
  await router.request('market.research.cancel', { id });
  assert.equal((await screen).cancelled, true);
  assert.equal(services[0].pending.length, 0);
});
