'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { StateStore } = require('../desktop/state-store.cjs');

const state = (text = '记录我的判断') => ({
  version: 1, goal: 8, stages: 20, startCapital: 1000000,
  account: '实盘主账户', compact: false, reduced: false, largeText: false,
  concurrency: 2, notes: { A08: text }, tasks: [], taskTrash: [],
  watch: ['600900'], reports: [], page: 'A08', stock: '600900',
  effects: { enabled: true }, milestoneEvents: { '1': [{ title: '阶段笔记' }] },
});

function fixture(t) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'atrade-state-test-'));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  return new StateStore(path.join(directory, 'workspace'));
}

function writeEnvelope(file, content, extra = {}) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, JSON.stringify({ schemaVersion: 1, scope: 'desktop-demo-workspace', savedAt: new Date().toISOString(), state: content, ...extra }));
}

test('empty workspace and saved notes/configuration survive reopening', (t) => {
  const store = fixture(t);
  assert.deepEqual(store.load(), { state: null, warning: null });
  const input = state();
  const saved = store.save(input);
  assert.ok(Number.isFinite(Date.parse(saved.savedAt)));
  assert.deepEqual(new StateStore(store.paths.directory).load(), { state: input, warning: null });
  assert.deepEqual(JSON.parse(fs.readFileSync(store.paths.backupFile)).state, input);
  assert.equal(JSON.parse(fs.readFileSync(store.paths.file)).scope, 'desktop-demo-workspace');
  assert.deepEqual(fs.readdirSync(store.paths.directory).sort(), ['workspace-state.json', 'workspace-state.previous.json']);
});

test('second save keeps the preceding valid version and recovers damaged main', (t) => {
  const store = fixture(t);
  store.save(state('第一版'));
  store.save(state('第二版'));
  assert.equal(JSON.parse(fs.readFileSync(store.paths.backupFile)).state.notes.A08, '第一版');
  const broken = '{"interrupted';
  fs.writeFileSync(store.paths.file, broken);
  const recovered = new StateStore(store.paths.directory).load();
  assert.deepEqual(recovered.state, state('第一版'));
  assert.match(recovered.warning, /已从上一有效版本恢复/);
  const damagedFiles = fs.readdirSync(store.paths.directory).filter((name) => name.startsWith('workspace-state.corrupt-'));
  assert.equal(damagedFiles.length, 1);
  assert.equal(fs.readFileSync(path.join(store.paths.directory, damagedFiles[0]), 'utf8'), broken);
  assert.deepEqual(store.load(), { state: state('第一版'), warning: null });
});

test('market-qualified securities survive reopening without confusing identical codes', (t) => {
  const store = fixture(t);
  for (const stock of ['sh000001', 'sz000001', 'bj920001', '600900']) {
    store.save({ ...state(), page: 'G01', stock });
    assert.equal(new StateStore(store.paths.directory).load().state.stock, stock);
  }
  for (const stock of ['us000001', 'sh00001', '../sh000001']) {
    assert.throws(() => store.save({ ...state(), stock }));
  }
});

test('market display preferences survive restart and invalid preferences preserve the saved workspace', (t) => {
  const store = fixture(t), marketPreferences = {version:1, lists:{'stocks:stocks':{query:'长江',size:30,sort:'amount',order:'desc',compact:true,filtersOpen:false}}, studies:{ma:false,force:true,signals:false,box:true}};
  store.save({...state(),marketPreferences});
  assert.deepEqual(new StateStore(store.paths.directory).load().state.marketPreferences, marketPreferences);
  const before = fs.readFileSync(store.paths.file, 'utf8');
  assert.throws(() => store.save({...state(),marketPreferences:{...marketPreferences,lists:{'stocks:stocks':{size:500}}}}), {code:'INVALID_STATE'});
  assert.equal(fs.readFileSync(store.paths.file, 'utf8'), before);
});

test('missing main is recovered from a valid backup', (t) => {
  const store = fixture(t);
  store.save(state());
  fs.unlinkSync(store.paths.file);
  assert.match(store.load().warning, /文件缺失/);
  assert.deepEqual(new StateStore(store.paths.directory).load().state, state());
});

test('corruption without a valid backup blocks loading and saving without clearing files', (t) => {
  const store = fixture(t);
  store.save(state());
  fs.writeFileSync(store.paths.file, 'bad-main');
  fs.writeFileSync(store.paths.backupFile, 'bad-backup');
  assert.throws(() => store.load(), { code: 'NO_VALID_STATE' });
  assert.throws(() => store.save(state('overwrite')), { code: 'NO_VALID_STATE' });
  assert.equal(fs.readFileSync(store.paths.file, 'utf8'), 'bad-main');
  assert.equal(fs.readFileSync(store.paths.backupFile, 'utf8'), 'bad-backup');
  fs.unlinkSync(store.paths.file);
  assert.throws(() => store.load(), { code: 'NO_VALID_STATE' });
});

test('future schema and state versions block downgrade, including backup files', (t) => {
  const store = fixture(t);
  store.save(state());
  for (const file of [store.paths.file, store.paths.backupFile]) {
    for (const future of ['schema', 'state']) {
      writeEnvelope(store.paths.file, state());
      writeEnvelope(store.paths.backupFile, state());
      writeEnvelope(file, future === 'state' ? { ...state(), version: 2 } : state(), future === 'schema' ? { schemaVersion: 2 } : {});
      const before = fs.readFileSync(file, 'utf8');
      assert.throws(() => store.load(), { code: 'FUTURE_VERSION' });
      assert.throws(() => store.save(state('overwrite')), { code: 'FUTURE_VERSION' });
      assert.equal(fs.readFileSync(file, 'utf8'), before);
    }
  }
});

test('state validation rejects unsafe, invalid and excessive data before any file changes', (t) => {
  const store = fixture(t);
  store.save(state());
  const before = fs.readFileSync(store.paths.file, 'utf8');
  const invalid = [
    null, {}, { ...state(), version: 0 }, { ...state(), notes: [] },
    { ...state(), notes: { A08: 1 } }, { ...state(), tasks: [{}] },
    { ...state(), goal: 0 }, { ...state(), stages: 1.5 }, { ...state(), stages: 201 },
    { ...state(), startCapital: Infinity }, { ...state(), concurrency: 5 },
    { ...state(), compact: 'yes' }, { ...state(), watch: [42] },
    { ...state(), page: '<script>' }, { ...state(), stock: 600900 },
    { ...state(), ruleA: -1 }, { ...state(), reports: [null] },
    { ...state(), extra: new Date() }, { ...state(), extra: undefined },
    { ...state(), extra: JSON.parse('{"nested":{"__proto__":{"polluted":true}}}') },
    { ...state(), extra: JSON.parse('{"constructor":{}}') },
    { ...state(), extra: JSON.parse('{"prototype":{}}') },
  ];
  for (const input of invalid) assert.throws(() => store.save(input));
  const cyclic = state(); cyclic.extra = cyclic;
  assert.throws(() => store.save(cyclic));
  const accessor = state(); Object.defineProperty(accessor, 'extra', { enumerable: true, get() { throw new Error('getter must not run'); } });
  assert.throws(() => store.save(accessor), /访问器/);
  const hidden = state(); Object.defineProperty(hidden, 'toJSON', { value() { throw new Error('toJSON must not run'); } });
  assert.throws(() => store.save(hidden), /隐藏字段/);
  const hiddenUnsafe = state(); Object.defineProperty(hiddenUnsafe, '__proto__', { value: {} });
  assert.throws(() => store.save(hiddenUnsafe), /不安全字段/);
  assert.throws(() => store.save(state('a'.repeat(5 * 1024 * 1024))), { code: 'STATE_TOO_LARGE' });
  assert.equal(fs.readFileSync(store.paths.file, 'utf8'), before);
});

test('task progress and archived task structures are validated', (t) => {
  const store = fixture(t);
  const task = { id: 't1', name: '演示研究', status: 'paused', progress: 42.5, count: 1 };
  store.save({ ...state(), tasks: [task], taskTrash: [{ task: { ...task, id: 't2' }, index: 0, deletedAt: new Date().toISOString() }] });
  for (const invalidTask of [{ ...task, progress: 101 }, { ...task, status: 'unknown' }, { ...task, count: 0 }]) {
    assert.throws(() => store.save({ ...state(), tasks: [invalidTask] }));
  }
  assert.throws(() => store.save({ ...state(), tasks: [task, task] }));
  assert.throws(() => store.save({ ...state(), taskTrash: [{ task: null }] }));
});

test('oversized main is recovered and preserved, oversized backup alone blocks save', (t) => {
  const store = fixture(t);
  store.save(state());
  fs.writeFileSync(store.paths.file, Buffer.alloc(5 * 1024 * 1024 + 1, 32));
  assert.match(store.load().warning, /文件损坏/);
  const preserved = fs.readdirSync(store.paths.directory).find((name) => name.startsWith('workspace-state.corrupt-'));
  assert.equal(fs.statSync(path.join(store.paths.directory, preserved)).size, 5 * 1024 * 1024 + 1);
  fs.unlinkSync(store.paths.file);
  fs.writeFileSync(store.paths.backupFile, Buffer.alloc(5 * 1024 * 1024 + 1, 32));
  assert.throws(() => store.save(state()), { code: 'NO_VALID_STATE' });
});

test('identical state is a no-op, including after reopening the store', (t) => {
  const store = fixture(t);
  const first = store.save(state());
  const original = fs.renameSync;
  t.after(() => { fs.renameSync = original; });
  fs.renameSync = () => { throw new Error('identical state must not be written'); };
  assert.deepEqual(new StateStore(store.paths.directory).save(state()), first);
});

test('rename failure preserves the last main and leaves no temporary files', (t) => {
  const store = fixture(t);
  store.save(state('已保存'));
  const before = fs.readFileSync(store.paths.file, 'utf8');
  const original = fs.renameSync;
  t.after(() => { fs.renameSync = original; });
  fs.renameSync = (from, to) => {
    if (to === store.paths.file) throw Object.assign(new Error('simulated rename failure'), { code: 'EACCES' });
    return original(from, to);
  };
  assert.throws(() => store.save(state('尚未保存')), { code: 'EACCES' });
  assert.equal(fs.readFileSync(store.paths.file, 'utf8'), before);
  assert.equal(fs.readFileSync(store.paths.backupFile, 'utf8'), before);
  assert.ok(fs.readdirSync(store.paths.directory).every((name) => !name.endsWith('.tmp')));
});

test('fsync failure keeps existing files and removes incomplete temporary file', (t) => {
  const store = fixture(t);
  store.save(state('已保存'));
  const before = fs.readFileSync(store.paths.file, 'utf8');
  const backupBefore = fs.readFileSync(store.paths.backupFile, 'utf8');
  const original = fs.fsyncSync;
  t.after(() => { fs.fsyncSync = original; });
  fs.fsyncSync = () => { throw Object.assign(new Error('simulated disk failure'), { code: 'EIO' }); };
  assert.throws(() => store.save(state('尚未保存')), { code: 'EIO' });
  assert.equal(fs.readFileSync(store.paths.file, 'utf8'), before);
  assert.equal(fs.readFileSync(store.paths.backupFile, 'utf8'), backupBefore);
  assert.ok(fs.readdirSync(store.paths.directory).every((name) => !name.endsWith('.tmp')));
});

test('read permission errors are propagated and never treated as corrupted data', (t) => {
  const store = fixture(t);
  store.save(state());
  const original = fs.openSync;
  const before = fs.readFileSync(store.paths.file, 'utf8');
  t.after(() => { fs.openSync = original; });
  fs.openSync = (file, ...args) => {
    if (file === store.paths.file) throw Object.assign(new Error('simulated permission denied'), { code: 'EACCES' });
    return original(file, ...args);
  };
  assert.throws(() => store.load(), { code: 'EACCES' });
  assert.throws(() => store.save(state('overwrite')), { code: 'EACCES' });
  fs.openSync = original;
  assert.equal(fs.readFileSync(store.paths.file, 'utf8'), before);
  assert.equal(fs.readdirSync(store.paths.directory).length, 2);
});
