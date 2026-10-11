const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { BusinessService } = require('../desktop/business-service.cjs');

test('desktop pipe roundtrip persists real accounts and rejects unknown methods', async t => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'atrade-business-test-'));
  const service = new BusinessService(directory);
  t.after(async () => {
    const exited = new Promise(resolve => service.child.once('exit', resolve));
    service.close(); await exited;
    fs.rmSync(directory, { recursive: true, force: true });
  });
  const empty = await service.request('workspace');
  assert.equal(empty.ok, true);
  assert.equal(empty.data.accounts.length, 0);
  const saved = await service.request('account.save', { name: '测试实盘', kind: 'real', startDate: '2026-10-01', openingCash: 10000, goalPercent: 30, stages: [] });
  assert.equal(saved.ok, true, saved.error);
  assert.equal(saved.data.accounts[0].name, '测试实盘');
  assert.equal(saved.data.summary.cash, 10000);
  assert.equal((await service.request('filesystem.read', { path: 'secrets' })).ok, false);
  const invalid = await service.request('ledger.save', { accountId: saved.data.accountId, type: 'sell', code: '000001', date: '2026-10-02', quantity: 100, price: 10 });
  assert.equal(invalid.ok, false);
  assert.equal((await service.request('workspace')).data.ledger.length, 0);
});
