const test = require('node:test');
const assert = require('node:assert/strict');
const { createAccountValuation, MAX_POSITIONS, BAR_LIMIT, CONCURRENCY } = require('../desktop/account-valuation.cjs');
const { METHODS } = require('../desktop/business-service.cjs');

const DATE = '2026-10-09';
const REQUEST = { accountId: 'account-a', date: DATE };
const position = code => ({ code, quantity: 100, cost: 1000 });
const barResult = (symbol, rows, extra = {}) => ({ ok: true, data: { symbol, adjustment: 'none', rows, ...extra } });
function fixture(codes, bars, status = { configured: true }) {
  const calls = [];
  const positions = codes.map(position);
  const business = { async request(method, payload) {
    calls.push({ target: 'business', method, payload });
    assert.equal(method, 'account.asof', 'valuation never saves or scans');
    return { ok: true, data: { ...payload, positions } };
  } };
  const market = { async request(method, payload) {
    calls.push({ target: 'market', method, payload });
    if (method === 'market.status') return { ok: true, data: status };
    assert.equal(method, 'market.bars');
    return bars(payload);
  } };
  return { valuation: createAccountValuation({ business, market }), calls, positions };
}

test('valuation uses historical positions and latest nonfuture unadjusted close without writing', async () => {
  const { valuation, calls, positions } = fixture(['600000', '000001', '920001'], ({ symbol, end, period, limit }) => {
    assert.equal(end, DATE); assert.equal(period, '1d'); assert.equal(limit, BAR_LIMIT);
    return barResult(symbol, [
      { date: '2026-10-12', close: 900 }, { date: '2026-10-08', close: 11.2 },
      { date: '2026-10-07', close: 10 }, { date: DATE, close: null },
    ]);
  });
  const original = structuredClone(positions);
  const result = await valuation(REQUEST);
  assert.equal(result.ok, true);
  assert.deepEqual(result.data.prices, { '600000': 11.2, '000001': 11.2, '920001': 11.2 });
  assert.deepEqual(result.data.priceDetails['600000'], {
    source: 'tdx_day', priceDate: '2026-10-08', requestedDate: DATE, adjustment: 'none', stale: true,
  });
  assert.deepEqual(result.data.missingPrices, []);
  assert.deepEqual(positions, original);
  assert.deepEqual(calls[0], { target: 'business', method: 'account.asof', payload: REQUEST });
  assert.deepEqual(calls.filter(call => call.method === 'market.bars').map(call => call.payload.symbol), ['sh600000', 'sz000001', 'bj920001']);
  assert.equal(METHODS.has('account.valuation'), false, 'orchestration is only routed in the main process');
  assert.equal(METHODS.has('account.asof'), true);
});

test('no configuration or no positions produces no market bar requests and no invented prices', async () => {
  const missing = fixture(['600000'], () => { throw new Error('must not read'); }, { configured: false, online: { enabled: false } });
  const result = await missing.valuation(REQUEST);
  assert.deepEqual(result.data.prices, {});
  assert.deepEqual(result.data.missingPrices, ['600000']);
  assert.equal(missing.calls.length, 2);
  const empty = fixture([], () => { throw new Error('must not read'); });
  assert.deepEqual((await empty.valuation(REQUEST)).data.positions, []);
  assert.equal(empty.calls.length, 1);
});

test('single-symbol failure, future-only data, adjusted prices and identity mismatch do not erase other prices', async () => {
  const { valuation } = fixture(['600000', '600001', '600002', '600003', '600004'], ({ symbol }) => {
    if (symbol === 'sh600001') return { ok: false, error: 'unavailable' };
    if (symbol === 'sh600002') return barResult(symbol, [{ date: '2026-10-12', close: 99 }]);
    if (symbol === 'sh600003') return barResult(symbol, [{ date: DATE, close: 99 }], { adjustment: 'qfq' });
    if (symbol === 'sh600004') return barResult('sz000001', [{ date: DATE, close: 99 }]);
    return barResult(symbol, [{ date: DATE, close: 12 }], { provider: 'tdx_mcp', stale: true });
  }, { configured: false, online: { enabled: true } });
  const result = await valuation(REQUEST);
  assert.equal(result.ok, true);
  assert.deepEqual(result.data.prices, { '600000': 12 });
  assert.deepEqual(result.data.missingPrices, ['600001', '600002', '600003', '600004']);
  assert.equal(result.data.priceDetails['600000'].source, 'tdx_mcp');
  assert.equal(result.data.priceDetails['600000'].stale, true);
});

test('valuation bounds concurrency and coalesces duplicate requests without cross-account sharing', async () => {
  let active = 0, peak = 0, release;
  const gate = new Promise(resolve => { release = resolve; });
  const { valuation, calls } = fixture(['600000', '600001', '600002', '600003'], async ({ symbol }) => {
    active++; peak = Math.max(peak, active);
    await gate;
    active--;
    return barResult(symbol, [{ date: DATE, close: 10 }]);
  });
  const first = valuation(REQUEST), duplicate = valuation({ ...REQUEST });
  const other = await valuation({ ...REQUEST, accountId: 'account-b' });
  assert.equal(other.ok, false);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(peak, CONCURRENCY);
  release();
  assert.deepEqual(await first, await duplicate);
  assert.equal(calls.filter(call => call.method === 'account.asof').length, 1);
  assert.equal(calls.filter(call => call.method === 'market.bars').length, 4);
});

test('invalid dates, oversize holdings and oversize bar responses are bounded', async () => {
  const tooMany = fixture(Array.from({ length: MAX_POSITIONS + 1 }, (_, index) => String(600000 + index)), () => { throw new Error('must not read'); });
  assert.equal((await tooMany.valuation({ ...REQUEST, date: '2026-02-30' })).ok, false);
  assert.equal(tooMany.calls.length, 0);
  assert.equal((await tooMany.valuation(REQUEST)).ok, false);
  assert.equal(tooMany.calls.length, 1);
  const tooLarge = fixture(['600000'], ({ symbol }) => barResult(symbol, Array.from({ length: BAR_LIMIT + 1 }, () => ({ date: DATE, close: 1 }))));
  assert.deepEqual((await tooLarge.valuation(REQUEST)).data.missingPrices, ['600000']);
});

test('unknown instruments require manual pricing and account mismatch refuses data', async () => {
  const unknown = fixture(['999999'], () => { throw new Error('must not guess'); });
  assert.deepEqual((await unknown.valuation(REQUEST)).data.missingPrices, ['999999']);
  assert.equal(unknown.calls.some(call => call.method === 'market.bars'), false);
  const valuation = createAccountValuation({
    business: { request: async () => ({ ok: true, data: { accountId: 'other', date: DATE, positions: [] } }) },
    market: { request: async () => { throw new Error('must not read'); } },
  });
  assert.equal((await valuation(REQUEST)).ok, false);
});
