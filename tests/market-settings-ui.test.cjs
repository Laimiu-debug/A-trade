const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

async function fixture({followLocal = true} = {}) {
  const listeners = {}, calls = [];
  const status = {path: 'C:\\private-local', configured: true, available: true, lastScannedAt: '2026-10-11T01:00:00Z', online: {
    root: followLocal ? 'C:\\private-local' : 'C:\\private-terminal', configuredRoot: followLocal ? '' : 'C:\\private-terminal',
    followLocal, enabled: true, connected: false, available: true, refreshSeconds: 30, lastCheckedAt: null,
  }};
  const form = {id: 'market-online-form', elements: {root: {value: status.online.configuredRoot},
    followLocal: {checked: followLocal}, enabled: {checked: true}, refreshSeconds: {value: '30'}}};
  const document = {addEventListener(name, handler) {(listeners[name] ||= []).push(handler);},
    querySelector(selector) {return selector === '#market-online-form' ? form : null;}};
  const AT = {isDesktop: true, pages: {G01: {}}, state: {page: 'C01'}, groups: [{id: 'C', pages: [{id: 'C01', title: '数据源'}]}],
    escape: value => String(value ?? '').replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c])),
    icon: () => '', button: (text, _icon, attrs) => `<button ${attrs}>${text}</button>`, head: () => '', view: () => '', render() {}};
  const bridge = {async market(method, payload) {
    calls.push({method, payload: structuredClone(payload)});
    if (method === 'market.online.configure') {
      status.online = {...status.online, ...payload, configuredRoot: payload.root, root: payload.followLocal ? status.path : payload.root};
      return {ok: true, data: structuredClone(status.online)};
    }
    return {ok: true, data: structuredClone(status)};
  }};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../preview/market-module.js'), 'utf8'), {
    AT, document, window: {atradeDesktop: bridge, addEventListener() {}}, structuredClone,
    setInterval() {}, setTimeout, clearTimeout, queueMicrotask() {}, performance: {now: () => 1000},
  });
  await AT.marketUI.refresh();
  const dispatch = (name, target) => {for (const handler of listeners[name] || []) handler({target, preventDefault() {}, stopPropagation() {}});};
  return {AT, calls, form, status, edit(name, value) {
    const input = form.elements[name];
    if (typeof value === 'boolean') input.checked = value; else input.value = value;
    input.form = form; input.matches = selector => selector === '[data-market-online-field]'; dispatch('input', input);
  }, async save() {dispatch('submit', form); await new Promise(resolve => setImmediate(resolve));}};
}

test('MCP inheritance remains explicit across settings navigation and sends empty stored root', async () => {
  const f = await fixture({followLocal: false});
  assert.match(f.AT.view('C01'), /当前生效目录：C:\\private-terminal/);
  f.edit('followLocal', true);
  assert.equal(f.form.elements.root.disabled, true);
  f.AT.state.page = 'C02'; f.AT.view('C02'); f.AT.state.page = 'C01';
  const html = f.AT.view('C01');
  assert.match(html, /name="followLocal"[^>]*checked/);
  assert.match(html, /name="root"[^>]*disabled/);
  await f.save();
  const payload = f.calls.find(call => call.method === 'market.online.configure').payload;
  assert.equal(payload.followLocal, true);
  assert.equal(payload.root, '');
  assert.match(f.AT.view('C01'), /当前生效目录：C:\\private-local/);
});

test('independent MCP directory must be provided explicitly', async () => {
  const f = await fixture();
  f.edit('followLocal', false);
  assert.equal(f.form.elements.root.disabled, false);
  await f.save();
  assert.equal(f.calls.filter(call => call.method === 'market.online.configure').length, 0);
  assert.match(f.AT.view('C01'), /请填写独立通达信目录/);
  f.edit('root', 'C:\\chosen-terminal'); await f.save();
  const payload = f.calls.find(call => call.method === 'market.online.configure').payload;
  assert.equal(payload.followLocal, false);
  assert.equal(payload.root, 'C:\\chosen-terminal');
});

test('market diagnostics exposes status and timestamps without directory paths', async () => {
  const f = await fixture();
  const diagnostic = f.AT.marketUI.diagnostics();
  assert.equal(diagnostic.local.loaded, true);
  assert.equal(diagnostic.local.lastScanAt, f.status.lastScannedAt);
  assert.equal(diagnostic.online.followLocal, true);
  assert.doesNotMatch(JSON.stringify(diagnostic), /private|root|path/);
});
