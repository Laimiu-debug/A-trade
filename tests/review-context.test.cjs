const test = require('node:test');
const assert = require('node:assert/strict');
const { buildReviewContext } = require('../desktop/review-context.cjs');

test('AI review includes earlier plans linked to current effective trades and retains period plans', () => {
  const workspace = {
    ledger: [
      { id: 'buy-current', date: '2026-10-09', type: 'buy' },
      { id: 'buy-selected', date: '2026-09-30', type: 'buy' },
      { id: 'other-trade', date: '2026-09-20', type: 'sell' },
      { id: 'voided', date: '2026-10-09', type: 'buy', voided: true },
      { id: 'cash', date: '2026-10-09', type: 'deposit' },
    ],
    plans: [
      { id: 'period', date: '2026-10-09', linkedTradeIds: [] },
      { id: 'earlier-current', date: '2026-10-08', linkedTradeIds: ['buy-current'] },
      { id: 'earlier-selected', date: '2026-09-29', linkedTradeIds: ['buy-selected'] },
      { id: 'unrelated', date: '2026-09-19', linkedTradeIds: ['other-trade'] },
      { id: 'voided-only', date: '2026-10-08', linkedTradeIds: ['voided'] },
      { id: 'cash-only', date: '2026-10-08', linkedTradeIds: ['cash'] },
      { id: 'missing-only', date: '2026-10-08', linkedTradeIds: ['missing'] },
    ],
    snapshots: [{ date: '2026-10-08' }, { date: '2026-10-09' }],
  };
  const original = structuredClone(workspace);
  const context = buildReviewContext(workspace, { date: '2026-10-09', relatedTradeIds: ['buy-selected', 'voided', 'missing'] });
  assert.deepEqual(context.plans.map(plan => plan.id), ['period', 'earlier-current', 'earlier-selected']);
  assert.deepEqual(context.ledger.map(trade => trade.id), ['buy-current', 'buy-selected', 'cash']);
  assert.deepEqual(context.snapshots, [{ date: '2026-10-09' }]);
  assert.deepEqual(workspace, original);
});

test('review period remains inclusive and missing optional data stays empty', () => {
  const context = buildReviewContext({ plans: [{ date: '2026-10-05' }, { date: '2026-10-09' }, { date: '2026-10-10' }] },
    { date: '2026-10-05', endDate: '2026-10-09' });
  assert.deepEqual(context.plans, [{ date: '2026-10-05' }, { date: '2026-10-09' }]);
  assert.deepEqual(context.ledger, []);
  assert.deepEqual(context.snapshots, []);
  assert.equal(context.marketDataAvailable, false);
});

test('historical review omits execution aggregates containing future or excluded unsent trades', () => {
  const retainedExecution = { quantity: 100, averagePrice: 10, matchedTradeIds: ['current'], excludedTradeIds: [], warnings: [] };
  const workspace = {
    ledger: [
      { id: 'current', date: '2026-10-09', type: 'buy' },
      { id: 'future', date: '2026-10-12', type: 'buy' },
    ],
    plans: [
      { id: 'historical', date: '2026-10-09', linkedTradeIds: ['current', 'future'],
        execution: { quantity: 200, averagePrice: 50, matchedTradeIds: ['current', 'future'], excludedTradeIds: [] } },
      { id: 'prior-linked', date: '2026-10-08', linkedTradeIds: ['current'], execution: retainedExecution },
      { id: 'excluded-future', date: '2026-10-09', linkedTradeIds: ['current', 'future'],
        execution: { quantity: 100, matchedTradeIds: ['current'], excludedTradeIds: ['future'], warnings: ['方向不符：future'] } },
    ],
  };
  const original = structuredClone(workspace);
  const context = buildReviewContext(workspace, { date: '2026-10-09' });
  assert.deepEqual(context.ledger.map(item => item.id), ['current']);
  assert.deepEqual(context.plans.map(item => item.id), ['historical', 'prior-linked', 'excluded-future']);
  for (const plan of [context.plans[0], context.plans[2]]) {
    assert.equal(plan.execution, undefined);
    assert.match(plan.executionContextNote, /仅依据本次 ledger/);
    assert.deepEqual(plan.linkedTradeIds, ['current']);
    assert.doesNotMatch(JSON.stringify(plan), /"future"/, 'unsent trade ids are not included');
  }
  assert.deepEqual(context.plans[1].execution, retainedExecution);
  assert.deepEqual(workspace, original, 'context filtering never mutates saved plans or executions');
});
