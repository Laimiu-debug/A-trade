const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { BusinessService } = require('../desktop/business-service.cjs');

test('experimental catalogue and current scans use the real private research pipe without automatic replay', async t => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'atrade-experimental-pipe-'));
  const service = new BusinessService(directory, { mode: 'research' });
  t.after(async () => {
    const exited = new Promise(resolve => service.child.once('exit', resolve));
    service.close(); await exited;
    const target = path.resolve(directory), parent = path.resolve(os.tmpdir());
    assert.equal(path.dirname(target), parent);
    assert.ok(path.basename(target).startsWith('atrade-experimental-pipe-'));
    fs.rmSync(target, { recursive: true, force: true });
  });
  const catalogue = await service.request('research.experimental.catalogue');
  assert.equal(catalogue.ok, true, catalogue.error);
  assert.deepEqual(catalogue.data.strategies.map(s => s.id), [
    'contractionNoProgress5', 'persistentLeaderTrail10', 'strongReclaimVolatilityTrail']);
  assert.deepEqual(catalogue.data.strategies.map(s => s.sources.length), [2, 6, 6]);
  assert.equal(catalogue.data.automaticExecution, false);
  const historical = await service.request('research.experimental.list');
  const scans = await service.request('research.experimental.scan.list');
  assert.deepEqual(historical.data.tasks, []);
  assert.deepEqual(scans.data.tasks, []);
  const badSource = await service.request('research.experimental.create', {
    strategyId: 'contractionNoProgress5', sourceId: 'unregistered', requestId: 'invalid-source'});
  assert.equal(badSource.ok, false);
  assert.equal((await service.request('research.experimental.scan.create', {requestId:'bad', start:'2024-01-01'})).ok, false);
  assert.equal((await service.request('research.experimental.scan.filesystem', {path:'forbidden'})).ok, false);
  // No market configuration: this is a real failure path, never synthetic stock output.
  const created = await service.request('research.experimental.scan.create', {requestId:'empty-market'});
  assert.equal(created.ok, true, created.error);
  assert.equal(created.data.kind, 'currentScan');
  let detail;
  for (let attempt=0; attempt<100; attempt++) {
    detail = await service.request('research.experimental.scan.get', {id:created.data.id});
    if (['failed','completed'].includes(detail.data.task.status)) break;
    await new Promise(resolve => setTimeout(resolve, 25));
  }
  assert.equal(detail.data.task.status, 'failed');
  assert.match(detail.data.task.error, /通达信|行情/);
  assert.equal(detail.data.result, undefined);
  assert.equal((await service.request('research.experimental.scan.create', {requestId:'empty-market'})).data.id, created.data.id);
  assert.deepEqual((await service.request('research.experimental.list')).data.tasks, []);
});
